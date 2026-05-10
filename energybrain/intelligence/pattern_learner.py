"""PatternLearner — 5 GradientBoosting models for usage pattern prediction.

Three activation layers:
  Layer 1 (14+ days): weekday + hour + outdoor_temp (3 features)
  Layer 2 (90+ days): all 9 features
  Layer 3 (365+ days): separate model instances per is_school_holiday

Five models:
  dhw_model:        GBC → P(DHW boost needed in next 2h) [0-1]
  dishwasher_model: GBC → P(dishwasher loaded today) [0-1]
  washing_model:    GBC → P(washing machine loaded today) [0-1]
  dryer_model:      GBC → P(dryer loaded today) [0-1]
  cooking_model:    GBR → expected cooking peak hour [15.5-20.5]

All models use GBC_PARAMS hyperparameters — conservative to prevent overfitting.
Fallback defaults used until model is trained.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.preprocessing import StandardScaler

from energybrain.models import ApplianceType
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

MIN_DAYS_BASIC = 14
MIN_DAYS_SEASONAL = 90
MIN_DAYS_YEARLY = 365

GBC_PARAMS = {
    "n_estimators": 100,
    "max_depth": 3,
    "learning_rate": 0.05,
    "min_samples_leaf": 5,
    "subsample": 0.8,
    "random_state": 42,
}

DEFAULTS = {
    "dhw_demand": 0.5,
    "appliance_loading": 0.3,
    "cooking_peak_start": 17,
    "cooking_peak_end": 19,
}

_MODEL_KEYS = ["dhw", "dishwasher", "washing", "dryer", "cooking"]


class PatternLearner:
    """Learns usage patterns via GradientBoosting. Improves over time as data accumulates."""

    def __init__(self) -> None:
        self._models: dict[str, Optional[GradientBoostingClassifier | GradientBoostingRegressor]] = {
            k: None for k in _MODEL_KEYS
        }
        self._scalers: dict[str, StandardScaler] = {k: StandardScaler() for k in _MODEL_KEYS}
        self._days_of_data = 0
        self._n_features = 3  # Updated by update_patterns; used to truncate prediction vectors
        self._log = get_logger("pattern_learner")

    def is_trained(self, model_key: str = "dhw") -> bool:
        return self._models.get(model_key) is not None

    def predict_dhw_demand(
        self,
        weekday: int,
        hour: int,
        outdoor_temp_c: float,
        cloud_cover_pct: float = 50.0,
        wind_speed_ms: float = 3.0,
        is_school_holiday: bool = False,
        season_q: int = 1,
        baseline_power_w: float = 500.0,
        temp_vs_seasonal_avg: float = 0.0,
    ) -> float:
        """P(DHW boost needed in next 2 hours). Returns 0.5 if not trained."""
        if self._models["dhw"] is None:
            return DEFAULTS["dhw_demand"]
        features = self._make_features(
            weekday, hour, outdoor_temp_c, cloud_cover_pct, wind_speed_ms,
            is_school_holiday, season_q, baseline_power_w, temp_vs_seasonal_avg,
        )[:self._n_features]
        scaled = self._scalers["dhw"].transform([features])
        prob = self._models["dhw"].predict_proba(scaled)[0]
        return float(prob[1] if len(prob) > 1 else prob[0])

    def predict_appliance_loading(
        self,
        appliance: ApplianceType,
        weekday: int,
        outdoor_temp_c: float,
        cloud_cover_pct: float = 50.0,
        wind_speed_ms: float = 3.0,
        is_school_holiday: bool = False,
        season_q: int = 1,
        baseline_power_w: float = 500.0,
        temp_vs_seasonal_avg: float = 0.0,
    ) -> float:
        """P(appliance will be loaded today). Returns 0.3 if not trained."""
        key = self._appliance_key(appliance)
        if self._models[key] is None:
            return DEFAULTS["appliance_loading"]
        features = self._make_features(
            weekday, 12, outdoor_temp_c, cloud_cover_pct, wind_speed_ms,
            is_school_holiday, season_q, baseline_power_w, temp_vs_seasonal_avg,
        )[:self._n_features]
        scaled = self._scalers[key].transform([features])
        prob = self._models[key].predict_proba(scaled)[0]
        return float(prob[1] if len(prob) > 1 else prob[0])

    def get_cooking_peak(
        self,
        weekday: int,
        outdoor_temp_c: float = 10.0,
        cloud_cover_pct: float = 50.0,
        is_school_holiday: bool = False,
        season_q: int = 1,
    ) -> tuple[int, int]:
        """Return (start_hour, end_hour) for predicted cooking peak."""
        if self._models["cooking"] is None:
            return DEFAULTS["cooking_peak_start"], DEFAULTS["cooking_peak_end"]
        features = self._make_features(weekday, 12, outdoor_temp_c, cloud_cover_pct, 3.0,
                                        is_school_holiday, season_q, 500.0, 0.0)[:self._n_features]
        scaled = self._scalers["cooking"].transform([features])
        peak_hour = float(self._models["cooking"].predict(scaled)[0])
        peak_hour = max(15.0, min(20.5, peak_hour))
        start = int(peak_hour)
        return start, start + 2

    def update_patterns(self, training_data: list[dict]) -> None:
        """Retrain all models from observation dicts.

        Each dict should have keys: weekday, hour, outdoor_temp_c, cloud_cover_pct,
        wind_speed_ms, is_school_holiday, season_q, baseline_power_w,
        temp_vs_seasonal_avg, dhw_needed, dishwasher_loaded, washing_loaded,
        dryer_loaded, cooking_peak_hour.
        """
        if not training_data:
            return

        self._days_of_data = len(set(
            str(d.get("date", i)) for i, d in enumerate(training_data)
        ))

        n_features = 3 if self._days_of_data < MIN_DAYS_SEASONAL else 9
        self._n_features = n_features
        self._train_classifiers(training_data, n_features)
        self._log.info(
            "pattern_learner_trained",
            days=self._days_of_data,
            n_features=n_features,
            models_trained=sum(1 for m in self._models.values() if m is not None),
        )

    def get_feature_importances(self) -> dict[str, dict[str, float]]:
        """Return feature importances per trained model."""
        all_features = [
            "weekday", "hour", "outdoor_temp_c", "cloud_cover_pct",
            "wind_speed_ms", "is_school_holiday", "season_q",
            "baseline_power_w", "temp_vs_seasonal_avg",
        ]
        result: dict[str, dict[str, float]] = {}
        for key, model in self._models.items():
            if model is None or not hasattr(model, "feature_importances_"):
                continue
            importances = model.feature_importances_
            n = min(len(importances), len(all_features))
            result[key] = {all_features[i]: float(importances[i]) for i in range(n)}
        return result

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _make_features(
        weekday: int,
        hour: int,
        outdoor_temp_c: float,
        cloud_cover_pct: float,
        wind_speed_ms: float,
        is_school_holiday: bool,
        season_q: int,
        baseline_power_w: float,
        temp_vs_seasonal_avg: float,
    ) -> list[float]:
        return [
            float(weekday), float(hour), outdoor_temp_c, cloud_cover_pct,
            wind_speed_ms, float(is_school_holiday), float(season_q),
            baseline_power_w, temp_vs_seasonal_avg,
        ]

    @staticmethod
    def _appliance_key(appliance: ApplianceType) -> str:
        return {
            ApplianceType.DISHWASHER: "dishwasher",
            ApplianceType.WASHING_MACHINE: "washing",
            ApplianceType.DRYER: "dryer",
        }[appliance]

    def _train_classifiers(self, data: list[dict], n_features: int) -> None:
        feature_keys = [
            "weekday", "hour", "outdoor_temp_c", "cloud_cover_pct",
            "wind_speed_ms", "is_school_holiday", "season_q",
            "baseline_power_w", "temp_vs_seasonal_avg",
        ][:n_features]

        X = np.array([[float(d.get(k, 0)) for k in feature_keys] for d in data])

        # Binary classifiers
        for model_key, label_key in [
            ("dhw", "dhw_needed"),
            ("dishwasher", "dishwasher_loaded"),
            ("washing", "washing_loaded"),
            ("dryer", "dryer_loaded"),
        ]:
            y = np.array([int(bool(d.get(label_key, 0))) for d in data])
            if len(set(y)) < 2:
                continue  # Need both classes to train
            try:
                scaler = StandardScaler()
                X_scaled = scaler.fit_transform(X)
                clf = GradientBoostingClassifier(**GBC_PARAMS)
                clf.fit(X_scaled, y)
                self._models[model_key] = clf
                self._scalers[model_key] = scaler
            except Exception as exc:
                self._log.warning("pattern_train_failed", model=model_key, error=str(exc))

        # Cooking peak regressor
        y_cooking = np.array([float(d.get("cooking_peak_hour", 18.0)) for d in data])
        try:
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            reg = GradientBoostingRegressor(**{**GBC_PARAMS, "n_estimators": 100})
            reg.fit(X_scaled, y_cooking)
            self._models["cooking"] = reg
            self._scalers["cooking"] = scaler
        except Exception as exc:
            self._log.warning("pattern_train_failed", model="cooking", error=str(exc))
