"""StateStore — reads system state history from DB and builds training data.

Converts raw DB rows (system_states) into dicts usable by intelligence modules:
  - ThermalModel observations
  - PatternLearner training rows
  - PVForecaster calibration observations
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from energybrain.models import SystemState
from energybrain.persistence.database import DatabaseManager
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)


def _season_q(dt: datetime) -> int:
    """Return quarter (1-4) for a given datetime."""
    return (dt.month - 1) // 3 + 1


def _occupancy_type(is_school_holiday: bool) -> str:
    return "school_holiday" if is_school_holiday else "normal"


class StateStore:
    """Reads and aggregates system state history for ML training pipelines."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        self._log = get_logger("state_store")

    # ------------------------------------------------------------------
    # Persist current SystemState snapshot
    # ------------------------------------------------------------------

    async def save_state(self, state: SystemState) -> None:
        """Write full-resolution snapshot to system_states table."""
        appliances_running = {
            a.value: state.appliances[a].is_running
            for a in state.appliances
        }
        row: dict[str, Any] = {
            "timestamp": datetime.now().isoformat(),
            "pv_power_w": state.pv.power_w,
            "pv_daily_kwh": state.pv.daily_energy_kwh,
            "battery_soc_pct": state.battery.soc_pct,
            "battery_power_w": state.battery.power_w,
            "grid_power_w": state.grid.power_w,
            "indoor_temp_c": state.heat_pump.indoor_temp_c,
            "outdoor_temp_c": state.heat_pump.outdoor_temp_c,
            "hvac_setpoint_c": state.heat_pump.setpoint_c,
            "hvac_mode": state.heat_pump.hvac_mode.value,
            "hvac_regime": self._hvac_regime(state),
            "dhw_boost_active": int(state.heat_pump.dhw_boost_active),
            "dhw_temp_c": state.heat_pump.dhw_temp_c,
            "baseline_power_w": state.grid.power_w + state.pv.power_w,
            "occupancy_type": "normal",
            "dishwasher_running": int(
                state.appliances.get(
                    __import__("energybrain.models", fromlist=["ApplianceType"]).ApplianceType.DISHWASHER,
                    type("_", (), {"is_running": False})()
                ).is_running
            ),
            "washing_machine_running": int(
                state.appliances.get(
                    __import__("energybrain.models", fromlist=["ApplianceType"]).ApplianceType.WASHING_MACHINE,
                    type("_", (), {"is_running": False})()
                ).is_running
            ),
            "dryer_running": int(
                state.appliances.get(
                    __import__("energybrain.models", fromlist=["ApplianceType"]).ApplianceType.DRYER,
                    type("_", (), {"is_running": False})()
                ).is_running
            ),
        }
        await self._db.write_system_state(row)

    # ------------------------------------------------------------------
    # Build ML training data from DB rows
    # ------------------------------------------------------------------

    async def build_thermal_observations(self, days: int = 90) -> list[dict]:
        """Return ThermalModel training observations from recent DB rows.

        Computes delta_indoor_c_per_hour from consecutive readings.
        Requires at least 2 consecutive rows within 70-90s of each other.
        """
        rows = await self._fetch_system_states(days)
        observations = []
        for i in range(1, len(rows)):
            prev = rows[i - 1]
            curr = rows[i]
            try:
                dt_sec = (
                    datetime.fromisoformat(curr["timestamp"])
                    - datetime.fromisoformat(prev["timestamp"])
                ).total_seconds()
                if not (30 <= dt_sec <= 120):
                    continue
                dt_hours = dt_sec / 3600
                delta = (curr["indoor_temp_c"] - prev["indoor_temp_c"]) / dt_hours
                observations.append({
                    "outdoor_temp_c": float(curr["outdoor_temp_c"] or 10.0),
                    "solar_radiation_w_m2": max(0.0, float(curr["pv_power_w"] or 0.0)),
                    "wind_speed_ms": 3.0,  # Not stored — use mean
                    "hour_of_day": datetime.fromisoformat(curr["timestamp"]).hour,
                    "hvac_active": bool(curr["dhw_boost_active"] or
                                        (curr["hvac_mode"] in ("heat", "cool"))),
                    "indoor_temp_c": float(curr["indoor_temp_c"] or 20.0),
                    "delta_indoor_c_per_hour": delta,
                })
            except (TypeError, ValueError, KeyError):
                continue
        self._log.debug("thermal_observations_built", n=len(observations))
        return observations

    async def build_pattern_training_data(self, days: int = 90) -> list[dict]:
        """Return PatternLearner training rows (one per day with labels)."""
        rows = await self._fetch_system_states(days)
        if not rows:
            return []

        # Group by date
        by_date: dict[str, list[dict]] = {}
        for row in rows:
            date_str = row["timestamp"][:10]
            by_date.setdefault(date_str, []).append(row)

        training = []
        for date_str, day_rows in sorted(by_date.items()):
            dt = datetime.fromisoformat(date_str)
            # Aggregate daily features
            outdoor_temps = [r["outdoor_temp_c"] for r in day_rows if r.get("outdoor_temp_c") is not None]
            avg_outdoor = sum(outdoor_temps) / len(outdoor_temps) if outdoor_temps else 10.0
            avg_baseline = sum(
                r["baseline_power_w"] for r in day_rows if r.get("baseline_power_w") is not None
            ) / max(1, len(day_rows))

            dhw_boosts = [r for r in day_rows if r.get("dhw_boost_active")]
            dishwasher_on = any(r.get("dishwasher_running") for r in day_rows)
            washing_on = any(r.get("washing_machine_running") for r in day_rows)
            dryer_on = any(r.get("dryer_running") for r in day_rows)

            # Infer cooking peak (hour with highest baseline during 17-20h)
            evening_rows = [r for r in day_rows
                            if 17 <= datetime.fromisoformat(r["timestamp"]).hour <= 20]
            if evening_rows:
                peak_row = max(evening_rows, key=lambda r: r.get("baseline_power_w") or 0)
                cooking_peak_hour = float(datetime.fromisoformat(peak_row["timestamp"]).hour)
            else:
                cooking_peak_hour = 18.0

            training.append({
                "date": date_str,
                "weekday": dt.weekday(),
                "hour": 12,
                "outdoor_temp_c": avg_outdoor,
                "cloud_cover_pct": 50.0,
                "wind_speed_ms": 3.0,
                "is_school_holiday": False,
                "season_q": _season_q(dt),
                "baseline_power_w": avg_baseline,
                "temp_vs_seasonal_avg": 0.0,
                "dhw_needed": int(len(dhw_boosts) > 0),
                "dishwasher_loaded": int(dishwasher_on),
                "washing_loaded": int(washing_on),
                "dryer_loaded": int(dryer_on),
                "cooking_peak_hour": cooking_peak_hour,
            })
        self._log.debug("pattern_training_built", days=len(training))
        return training

    async def build_pv_calibration_row(
        self,
        date: datetime,
        predicted_kwh: float,
        actual_kwh: float,
    ) -> dict:
        """Build a calibration dict for PVForecaster.update_calibration()."""
        rows = await self._fetch_system_states_for_date(date)
        temps = [r["outdoor_temp_c"] for r in rows if r.get("outdoor_temp_c") is not None]
        avg_temp = sum(temps) / len(temps) if temps else 15.0
        return {
            "date": date,
            "predicted_kwh": predicted_kwh,
            "actual_kwh": actual_kwh,
            "avg_cloud_cover": 30.0,
            "avg_temp_c": avg_temp,
            "avg_wind_ms": 3.0,
        }

    # ------------------------------------------------------------------
    # Internal DB helpers
    # ------------------------------------------------------------------

    async def _fetch_system_states(self, days: int) -> list[dict]:
        """Fetch all system_states rows from the last N days."""
        conn = self._db._conn
        if conn is None:
            return []
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        rows = []
        try:
            async with conn.execute(
                "SELECT * FROM system_states WHERE timestamp >= ? ORDER BY timestamp ASC",
                (cutoff,),
            ) as cur:
                async for row in cur:
                    rows.append(dict(row))
        except Exception as exc:
            self._log.warning("fetch_system_states_failed", error=str(exc))
        return rows

    async def _fetch_system_states_for_date(self, date: datetime) -> list[dict]:
        """Fetch system_states rows for a specific calendar day."""
        date_str = date.strftime("%Y-%m-%d")
        conn = self._db._conn
        if conn is None:
            return []
        rows = []
        try:
            async with conn.execute(
                "SELECT * FROM system_states WHERE timestamp LIKE ? ORDER BY timestamp ASC",
                (f"{date_str}%",),
            ) as cur:
                async for row in cur:
                    rows.append(dict(row))
        except Exception as exc:
            self._log.warning("fetch_day_states_failed", error=str(exc))
        return rows

    @staticmethod
    def _hvac_regime(state: SystemState) -> str:
        from energybrain.models import HVACMode
        mode = state.heat_pump.hvac_mode
        if mode == HVACMode.HEAT:
            return "heating"
        if mode == HVACMode.COOL:
            return "cooling"
        return "idle"
