"""LearningStore — saves and loads ML model blobs via joblib + SQLite.

All ML models live in Layer 4 (never deleted):
  - thermal_model_snapshots  → ThermalModel sklearn object
  - pv_calibration_factors   → PVForecaster Ridge + scaler
  - pattern_learner_models   → PatternLearner GBC/GBR objects

Serialisation: joblib.dump/load to/from bytes in SQLite BLOB column.
"""
from __future__ import annotations

import io
import json
from datetime import datetime
from typing import Any, Optional

import joblib

from energybrain.persistence.database import DatabaseManager
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)


def _to_blob(obj: Any) -> bytes:
    buf = io.BytesIO()
    joblib.dump(obj, buf)
    return buf.getvalue()


def _from_blob(data: bytes) -> Any:
    return joblib.load(io.BytesIO(data))


def _season_q(dt: datetime) -> str:
    return f"Q{(dt.month - 1) // 3 + 1}"


class LearningStore:
    """Persists and loads ML model objects for all intelligence modules."""

    def __init__(self, db: DatabaseManager) -> None:
        self._db = db
        self._log = get_logger("learning_store")

    # ------------------------------------------------------------------
    # ThermalModel
    # ------------------------------------------------------------------

    async def save_thermal_model(self, thermal_model: Any) -> None:
        """Serialise and save ThermalModel to DB."""
        conn = self._db._conn
        if conn is None:
            return
        params = thermal_model._params
        blob = _to_blob(thermal_model._model) if thermal_model._model is not None else None
        now = datetime.now()
        try:
            await conn.execute(
                """INSERT INTO thermal_model_snapshots
                   (timestamp, season, occupancy_type, cooling_rate, heating_rate,
                    thermal_mass_hours, r2_score, samples_count, model_type, model_blob)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    now.isoformat(),
                    _season_q(now),
                    "normal",
                    params.cooling_rate_c_per_hour,
                    params.heating_rate_c_per_hour,
                    params.thermal_mass_hours,
                    params.r2_score,
                    params.samples_count,
                    params.model_type,
                    blob,
                ),
            )
            await conn.commit()
            self._log.info("thermal_model_saved", season=_season_q(now), r2=round(params.r2_score, 3))
        except Exception as exc:
            self._log.warning("thermal_model_save_failed", error=str(exc))

    async def load_thermal_model(self, thermal_model: Any) -> bool:
        """Load the latest ThermalModel snapshot into the given instance.

        Returns True if a model was loaded.
        """
        conn = self._db._conn
        if conn is None:
            return False
        try:
            async with conn.execute(
                "SELECT * FROM thermal_model_snapshots ORDER BY id DESC LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
            if row is None:
                return False
            from energybrain.models import ThermalModelParams
            thermal_model._params = ThermalModelParams(
                cooling_rate_c_per_hour=row["cooling_rate"] or 0.3,
                heating_rate_c_per_hour=row["heating_rate"] or 0.15,
                thermal_mass_hours=row["thermal_mass_hours"] or 8.0,
                r2_score=row["r2_score"] or 0.0,
                samples_count=row["samples_count"] or 0,
                is_trained=bool(row["model_blob"]),
                model_type=row["model_type"] or "linear",
            )
            if row["model_blob"]:
                thermal_model._model = _from_blob(bytes(row["model_blob"]))
                thermal_model._is_gbr = row["model_type"] == "gradient_boosting"
            self._log.info("thermal_model_loaded", r2=round(thermal_model._params.r2_score, 3))
            return True
        except Exception as exc:
            self._log.warning("thermal_model_load_failed", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # PVForecaster
    # ------------------------------------------------------------------

    async def save_pv_forecaster(self, forecaster: Any) -> None:
        """Save PVForecaster Ridge model and scaler to DB."""
        conn = self._db._conn
        if conn is None:
            return
        if not forecaster.is_calibrated():
            return
        blob = _to_blob({"ridge": forecaster._ridge, "scaler": forecaster._scaler,
                          "observations": forecaster._calibration_observations})
        now = datetime.now()
        try:
            await conn.execute(
                """INSERT INTO pv_calibration_factors
                   (updated_at, season, cloud_category, calibration_factor, sample_count, ridge_model_blob)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    now.isoformat(),
                    _season_q(now),
                    "mixed",
                    float(len(forecaster._calibration_observations)),
                    len(forecaster._calibration_observations),
                    blob,
                ),
            )
            await conn.commit()
            self._log.info("pv_forecaster_saved",
                           samples=len(forecaster._calibration_observations))
        except Exception as exc:
            self._log.warning("pv_forecaster_save_failed", error=str(exc))

    async def load_pv_forecaster(self, forecaster: Any) -> bool:
        """Load the latest PVForecaster snapshot into the given instance."""
        conn = self._db._conn
        if conn is None:
            return False
        try:
            async with conn.execute(
                "SELECT * FROM pv_calibration_factors ORDER BY id DESC LIMIT 1"
            ) as cur:
                row = await cur.fetchone()
            if row is None or not row["ridge_model_blob"]:
                return False
            payload = _from_blob(bytes(row["ridge_model_blob"]))
            forecaster._ridge = payload["ridge"]
            forecaster._scaler = payload["scaler"]
            forecaster._calibration_observations = payload.get("observations", [])
            self._log.info("pv_forecaster_loaded",
                           samples=len(forecaster._calibration_observations))
            return True
        except Exception as exc:
            self._log.warning("pv_forecaster_load_failed", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # PatternLearner
    # ------------------------------------------------------------------

    async def save_pattern_learner(self, learner: Any) -> None:
        """Save all trained PatternLearner models to DB."""
        conn = self._db._conn
        if conn is None:
            return
        importances = learner.get_feature_importances()
        now = datetime.now()
        for model_key in ["dhw", "dishwasher", "washing", "dryer", "cooking"]:
            model = learner._models.get(model_key)
            if model is None:
                continue
            blob = _to_blob({"model": model, "scaler": learner._scalers[model_key],
                              "n_features": learner._n_features})
            try:
                await conn.execute(
                    """INSERT INTO pattern_learner_models
                       (updated_at, model_name, occupancy_type, samples_count,
                        accuracy_pct, feature_importances, model_blob)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        now.isoformat(),
                        model_key,
                        "normal",
                        learner._days_of_data,
                        0.0,
                        json.dumps(importances.get(model_key, {})),
                        blob,
                    ),
                )
            except Exception as exc:
                self._log.warning("pattern_model_save_failed", model=model_key, error=str(exc))
        try:
            await conn.commit()
            self._log.info("pattern_learner_saved", days=learner._days_of_data)
        except Exception as exc:
            self._log.warning("pattern_learner_commit_failed", error=str(exc))

    async def load_pattern_learner(self, learner: Any) -> bool:
        """Load the latest PatternLearner models into the given instance."""
        conn = self._db._conn
        if conn is None:
            return False
        loaded = 0
        for model_key in ["dhw", "dishwasher", "washing", "dryer", "cooking"]:
            try:
                async with conn.execute(
                    """SELECT * FROM pattern_learner_models WHERE model_name=?
                       ORDER BY id DESC LIMIT 1""",
                    (model_key,),
                ) as cur:
                    row = await cur.fetchone()
                if row is None or not row["model_blob"]:
                    continue
                payload = _from_blob(bytes(row["model_blob"]))
                learner._models[model_key] = payload["model"]
                learner._scalers[model_key] = payload["scaler"]
                learner._n_features = payload.get("n_features", 3)
                learner._days_of_data = row["samples_count"] or 0
                loaded += 1
            except Exception as exc:
                self._log.warning("pattern_model_load_failed", model=model_key, error=str(exc))
        if loaded:
            self._log.info("pattern_learner_loaded", models=loaded)
        return loaded > 0
