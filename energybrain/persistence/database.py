"""SQLite database manager with layered retention strategy.

Layer 1 — Full resolution (60 s):  90-day rolling window
Layer 2 — Hourly aggregates:       2-year window
Layer 3 — Daily summaries:         unlimited (small)
Layer 4 — Learned model params:    unlimited (never delete — this IS the memory)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from energybrain.exceptions import DatabaseError
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

# Schema version — increment when DDL changes
_SCHEMA_VERSION = 1

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- LAYER 1: Full resolution 60-s snapshots (90-day rolling)
CREATE TABLE IF NOT EXISTS system_states (
    id                      INTEGER PRIMARY KEY,
    timestamp               TEXT NOT NULL,
    pv_power_w              REAL,
    pv_daily_kwh            REAL,
    battery_soc_pct         REAL,
    battery_power_w         REAL,
    grid_power_w            REAL,
    indoor_temp_c           REAL,
    outdoor_temp_c          REAL,
    hvac_setpoint_c         REAL,
    hvac_mode               TEXT,
    hvac_regime             TEXT,
    dhw_boost_active        INTEGER,
    dhw_temp_c              REAL,
    baseline_power_w        REAL,
    occupancy_type          TEXT,
    dishwasher_running      INTEGER,
    washing_machine_running INTEGER,
    dryer_running           INTEGER
);
CREATE INDEX IF NOT EXISTS idx_system_states_ts ON system_states(timestamp);

-- LAYER 2: Hourly aggregates (2-year rolling)
CREATE TABLE IF NOT EXISTS hourly_aggregates (
    id                  INTEGER PRIMARY KEY,
    hour_start          TEXT NOT NULL,
    season              TEXT,
    weekday             INTEGER,
    occupancy_type      TEXT,
    avg_pv_power_w      REAL,
    avg_grid_power_w    REAL,
    avg_indoor_temp_c   REAL,
    avg_outdoor_temp_c  REAL,
    avg_baseline_power_w REAL,
    hvac_mode           TEXT,
    dhw_boost_active    INTEGER,
    appliances_running  TEXT
);
CREATE INDEX IF NOT EXISTS idx_hourly_aggregates_ts ON hourly_aggregates(hour_start);

-- LAYER 3: Daily summaries (unlimited)
CREATE TABLE IF NOT EXISTS daily_summaries (
    id                  INTEGER PRIMARY KEY,
    date                TEXT NOT NULL UNIQUE,
    season              TEXT,
    pv_total_kwh        REAL,
    grid_import_kwh     REAL,
    grid_export_kwh     REAL,
    self_use_pct        REAL,
    peak_demand_kw      REAL,
    dhw_boost_count     INTEGER,
    appliances_started  TEXT,
    vacation_day        INTEGER,
    estimated_savings_eur REAL
);

-- LAYER 4: Learned model parameters (never delete)
CREATE TABLE IF NOT EXISTS thermal_model_snapshots (
    id                  INTEGER PRIMARY KEY,
    timestamp           TEXT,
    season              TEXT,
    occupancy_type      TEXT,
    cooling_rate        REAL,
    heating_rate        REAL,
    thermal_mass_hours  REAL,
    r2_score            REAL,
    samples_count       INTEGER,
    model_type          TEXT,
    model_blob          BLOB
);

CREATE TABLE IF NOT EXISTS pv_calibration_factors (
    id                  INTEGER PRIMARY KEY,
    updated_at          TEXT,
    season              TEXT,
    cloud_category      TEXT,
    calibration_factor  REAL,
    sample_count        INTEGER,
    ridge_model_blob    BLOB
);

CREATE TABLE IF NOT EXISTS pattern_learner_models (
    id                  INTEGER PRIMARY KEY,
    updated_at          TEXT,
    model_name          TEXT,
    occupancy_type      TEXT,
    samples_count       INTEGER,
    accuracy_pct        REAL,
    feature_importances TEXT,
    model_blob          BLOB
);

CREATE TABLE IF NOT EXISTS usage_patterns (
    id                  INTEGER PRIMARY KEY,
    updated_at          TEXT,
    pattern_type        TEXT,
    appliance_type      TEXT,
    weekday             INTEGER,
    hour                INTEGER,
    season              TEXT,
    occupancy_type      TEXT,
    probability         REAL,
    sample_count        INTEGER
);

-- OutcomeTracker — feedback loop for all models
CREATE TABLE IF NOT EXISTS predictions (
    id              INTEGER PRIMARY KEY,
    prediction_id   TEXT UNIQUE,
    model_name      TEXT,
    features        TEXT,
    predicted_value REAL,
    predicted_at    TEXT,
    actual_value    REAL,
    outcome_at      TEXT,
    is_correct      INTEGER,
    weight          REAL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_predictions_model ON predictions(model_name);
CREATE INDEX IF NOT EXISTS idx_predictions_at    ON predictions(predicted_at);

CREATE TABLE IF NOT EXISTS accuracy_reports (
    id                                  INTEGER PRIMARY KEY,
    period_start                        TEXT,
    period_end                          TEXT,
    dhw_accuracy_pct                    REAL,
    appliance_loading_accuracy_pct      REAL,
    pv_forecast_accuracy_pct            REAL,
    cooking_peak_accuracy_pct           REAL,
    drift_detected                      TEXT,
    total_predictions                   INTEGER,
    estimated_savings_eur               REAL,
    feature_importances                 TEXT,
    generated_at                        TEXT,
    notification_sent                   INTEGER
);

-- BatteryDispatcher MPC plans (STUB until V154 firmware)
CREATE TABLE IF NOT EXISTS battery_dispatch_plans (
    id                  INTEGER PRIMARY KEY,
    date                TEXT,
    generated_at        TEXT,
    hourly_target_w     TEXT,
    expected_savings_eur REAL,
    peak_prevention_kw  REAL,
    is_stub             INTEGER,
    actual_soc_path     TEXT
);

-- Audit trail — no expiry
CREATE TABLE IF NOT EXISTS actions_taken (
    id              INTEGER PRIMARY KEY,
    timestamp       TEXT NOT NULL,
    action_type     TEXT,
    target_entity   TEXT,
    parameters      TEXT,
    is_stub         INTEGER,
    success         INTEGER,
    verified        INTEGER,
    retry_needed    INTEGER,
    reason          TEXT,
    horizon         TEXT
);
CREATE INDEX IF NOT EXISTS idx_actions_ts ON actions_taken(timestamp);

-- Appliance deadline tracking
CREATE TABLE IF NOT EXISTS appliance_waiting (
    id                      INTEGER PRIMARY KEY,
    appliance_type          TEXT,
    waiting_since           TEXT,
    started_at              TEXT,
    is_force_started        INTEGER,
    actual_wait_hours       REAL,
    surplus_at_start_w      REAL,
    cooking_peak_delayed    INTEGER
);

-- Capacity tariff events (per month)
CREATE TABLE IF NOT EXISTS capacity_tariff_events (
    id              INTEGER PRIMARY KEY,
    timestamp       TEXT,
    peak_kw         REAL,
    duration_minutes INTEGER,
    caused_by       TEXT,
    month           TEXT
);

-- Vacation periods (excluded from learning modules)
CREATE TABLE IF NOT EXISTS vacation_periods (
    id          INTEGER PRIMARY KEY,
    start_date  TEXT,
    end_date    TEXT,
    noted_at    TEXT
);

-- Safety events
CREATE TABLE IF NOT EXISTS safety_events (
    id                  INTEGER PRIMARY KEY,
    timestamp           TEXT,
    event_type          TEXT,
    severity            TEXT,
    message             TEXT,
    action_taken        TEXT,
    notification_sent   INTEGER
);

-- Heartbeat (downtime detection)
CREATE TABLE IF NOT EXISTS system_heartbeat (
    id          INTEGER PRIMARY KEY,
    timestamp   TEXT NOT NULL
);

-- Day plans archive
CREATE TABLE IF NOT EXISTS day_plans (
    id                      INTEGER PRIMARY KEY,
    date                    TEXT,
    generated_at            TEXT,
    total_pv_forecast_kwh   REAL,
    actual_pv_kwh           REAL,
    surplus_windows         TEXT,
    scheduled_tasks         TEXT,
    week_strategy_note      TEXT
);
"""


class DatabaseManager:
    """Async SQLite database manager for EnergyBrain.

    Usage::

        db = DatabaseManager(Path("energybrain.db"))
        await db.initialize()
        await db.write_heartbeat()
        await db.close()
    """

    def __init__(self, db_path: Path) -> None:
        self._path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Open the database, create tables, and run migrations.

        Raises:
            DatabaseError: If schema setup fails.
        """
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self._path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.executescript(_DDL)
            await self._apply_migrations()
            await self._conn.commit()
            logger.info("database_initialized", path=str(self._path))
        except Exception as exc:
            raise DatabaseError(f"Failed to initialize database: {exc}") from exc

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Migrations
    # ------------------------------------------------------------------

    async def _apply_migrations(self) -> None:
        """Ensure schema_version table reflects current version."""
        assert self._conn is not None
        async with self._conn.execute("SELECT version FROM schema_version") as cur:
            row = await cur.fetchone()
        current = row["version"] if row else 0
        if current < _SCHEMA_VERSION:
            await self._conn.execute(
                "INSERT OR REPLACE INTO schema_version(version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )
            logger.info("schema_migrated", from_version=current, to_version=_SCHEMA_VERSION)

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def write_heartbeat(self) -> None:
        """Write a heartbeat row (called every 60 s by the main loop).

        Raises:
            DatabaseError: On write failure.
        """
        await self._execute(
            "INSERT INTO system_heartbeat(timestamp) VALUES (?)",
            (datetime.now().isoformat(),),
        )

    async def get_last_heartbeat(self) -> Optional[datetime]:
        """Return the timestamp of the most recent heartbeat row."""
        row = await self._fetchone(
            "SELECT timestamp FROM system_heartbeat ORDER BY id DESC LIMIT 1"
        )
        return datetime.fromisoformat(row["timestamp"]) if row else None

    # ------------------------------------------------------------------
    # System states (Layer 1)
    # ------------------------------------------------------------------

    async def write_system_state(self, state: dict[str, Any]) -> None:
        """Insert a full-resolution system state snapshot.

        Args:
            state: Dict with keys matching system_states columns (excluding id).
        """
        cols = ", ".join(state.keys())
        placeholders = ", ".join("?" * len(state))
        await self._execute(
            f"INSERT INTO system_states({cols}) VALUES ({placeholders})",
            tuple(state.values()),
        )

    async def cleanup_old_states(self, retention_days: int = 90) -> int:
        """Delete system_states rows older than retention_days.

        Args:
            retention_days: Number of days to retain.

        Returns:
            Number of rows deleted.
        """
        cutoff = (datetime.now() - timedelta(days=retention_days)).isoformat()
        async with self._conn.execute(  # type: ignore[union-attr]
            "DELETE FROM system_states WHERE timestamp < ?", (cutoff,)
        ) as cur:
            deleted = cur.rowcount
        await self._conn.commit()  # type: ignore[union-attr]
        if deleted:
            logger.info("cleanup_system_states", deleted=deleted, cutoff=cutoff)
        return deleted

    # ------------------------------------------------------------------
    # Hourly aggregates (Layer 2)
    # ------------------------------------------------------------------

    async def write_hourly_aggregate(self, row: dict[str, Any]) -> None:
        """Insert a pre-computed hourly aggregate row."""
        cols = ", ".join(row.keys())
        placeholders = ", ".join("?" * len(row))
        await self._execute(
            f"INSERT INTO hourly_aggregates({cols}) VALUES ({placeholders})",
            tuple(row.values()),
        )

    async def cleanup_old_hourly(self, retention_years: int = 2) -> int:
        """Delete hourly_aggregates rows older than retention_years."""
        cutoff = (datetime.now() - timedelta(days=retention_years * 365)).isoformat()
        async with self._conn.execute(  # type: ignore[union-attr]
            "DELETE FROM hourly_aggregates WHERE hour_start < ?", (cutoff,)
        ) as cur:
            deleted = cur.rowcount
        await self._conn.commit()  # type: ignore[union-attr]
        return deleted

    # ------------------------------------------------------------------
    # Predictions (OutcomeTracker)
    # ------------------------------------------------------------------

    async def log_prediction(
        self,
        prediction_id: str,
        model_name: str,
        features: dict,
        predicted_value: float,
    ) -> None:
        """Log a prediction before the outcome is known.

        Args:
            prediction_id: UUID linking prediction to future outcome.
            model_name: One of dhw_demand|appliance_loading|cooking_peak|pv_forecast.
            features: Feature dict used for prediction.
            predicted_value: Predicted probability or value.
        """
        await self._execute(
            """INSERT OR IGNORE INTO predictions
               (prediction_id, model_name, features, predicted_value, predicted_at, weight)
               VALUES (?, ?, ?, ?, ?, 1.0)""",
            (
                prediction_id,
                model_name,
                json.dumps(features),
                predicted_value,
                datetime.now().isoformat(),
            ),
        )

    async def log_outcome(
        self,
        prediction_id: str,
        actual_value: float,
        is_correct: bool,
    ) -> None:
        """Fill in the actual outcome for a previously logged prediction.

        Args:
            prediction_id: UUID that was used when logging the prediction.
            actual_value: Observed actual value.
            is_correct: Whether the prediction was within tolerance.
        """
        await self._execute(
            """UPDATE predictions
               SET actual_value=?, outcome_at=?, is_correct=?
               WHERE prediction_id=?""",
            (
                actual_value,
                datetime.now().isoformat(),
                int(is_correct),
                prediction_id,
            ),
        )

    # ------------------------------------------------------------------
    # Accuracy reports
    # ------------------------------------------------------------------

    async def save_accuracy_report(self, report: dict[str, Any]) -> None:
        """Insert a monthly accuracy report.

        Args:
            report: Dict with keys matching accuracy_reports columns (excluding id).
        """
        cols = ", ".join(report.keys())
        placeholders = ", ".join("?" * len(report))
        await self._execute(
            f"INSERT INTO accuracy_reports({cols}) VALUES ({placeholders})",
            tuple(report.values()),
        )

    # ------------------------------------------------------------------
    # Safety events
    # ------------------------------------------------------------------

    async def log_safety_event(
        self,
        event_type: str,
        severity: str,
        message: str,
        action_taken: str = "",
        notification_sent: bool = False,
    ) -> None:
        """Record a safety event for audit purposes.

        Args:
            event_type: Type of safety event (e.g. indoor_temp_min_winter).
            severity: One of INFO|WARNING|CRITICAL.
            message: Human-readable description.
            action_taken: What action was taken in response.
            notification_sent: Whether a push notification was sent.
        """
        await self._execute(
            """INSERT INTO safety_events
               (timestamp, event_type, severity, message, action_taken, notification_sent)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                datetime.now().isoformat(),
                event_type,
                severity,
                message,
                action_taken,
                int(notification_sent),
            ),
        )

    # ------------------------------------------------------------------
    # Aggregation jobs (called nightly at 03:30)
    # ------------------------------------------------------------------

    async def aggregate_to_hourly(self, target_date: Optional[datetime] = None) -> int:
        """Aggregate yesterday's 60-s data into hourly_aggregates.

        Args:
            target_date: Date to aggregate (defaults to yesterday).

        Returns:
            Number of hours aggregated.
        """
        date = (target_date or datetime.now() - timedelta(days=1)).date()
        day_start = f"{date}T00:00:00"
        day_end = f"{date}T23:59:59"

        rows = await self._fetchall(
            """SELECT
                   strftime('%Y-%m-%dT%H:00:00', timestamp) AS hour_start,
                   AVG(pv_power_w)          AS avg_pv_power_w,
                   AVG(grid_power_w)        AS avg_grid_power_w,
                   AVG(indoor_temp_c)       AS avg_indoor_temp_c,
                   AVG(outdoor_temp_c)      AS avg_outdoor_temp_c,
                   AVG(baseline_power_w)    AS avg_baseline_power_w,
                   MAX(hvac_mode)           AS hvac_mode,
                   MAX(dhw_boost_active)    AS dhw_boost_active,
                   MAX(occupancy_type)      AS occupancy_type
               FROM system_states
               WHERE timestamp BETWEEN ? AND ?
               GROUP BY strftime('%Y-%m-%dT%H:00:00', timestamp)""",
            (day_start, day_end),
        )

        for row in rows:
            await self.write_hourly_aggregate(dict(row))

        if rows:
            await self._conn.commit()  # type: ignore[union-attr]
        logger.info("hourly_aggregation_done", date=str(date), hours=len(rows))
        return len(rows)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _execute(self, sql: str, params: tuple = ()) -> None:
        """Execute a write statement and commit.

        Raises:
            DatabaseError: On aiosqlite error.
        """
        if self._conn is None:
            raise DatabaseError("Database is not initialized — call initialize() first")
        try:
            await self._conn.execute(sql, params)
            await self._conn.commit()
        except aiosqlite.Error as exc:
            raise DatabaseError(f"Query failed: {exc}\nSQL: {sql}") from exc

    async def _fetchone(self, sql: str, params: tuple = ()) -> Optional[aiosqlite.Row]:
        """Fetch a single row."""
        if self._conn is None:
            raise DatabaseError("Database is not initialized")
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def _fetchall(self, sql: str, params: tuple = ()) -> list[aiosqlite.Row]:
        """Fetch all rows."""
        if self._conn is None:
            raise DatabaseError("Database is not initialized")
        async with self._conn.execute(sql, params) as cur:
            return await cur.fetchall()
