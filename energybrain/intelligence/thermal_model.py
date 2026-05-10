"""ThermalModel — learns and predicts house thermal behaviour.

Phase 1 (day 15-90): LinearRegression on 5 features.
Phase 2 (day 90+): Upgrade to GradientBoostingRegressor if R² < 0.85.

5 features:
  outdoor_temp_c, solar_radiation_w_m2, wind_speed_ms, hour_of_day, hvac_active

Target: delta_indoor_c_per_hour (change in indoor temp per hour).

DHW sub-model: Newton's law of cooling (physics-informed, always linear).
Fallback: ThermalModelParams defaults when is_ready() is False.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler

from energybrain.models import ThermalModelParams
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

MIN_SAMPLES = 336               # 7 days × 48 readings
R2_UPGRADE_THRESHOLD = 0.85     # Below this after 90 days → GBR

# DHW Newton cooling constant (estimated; learned from data when enough samples)
_DEFAULT_DHW_COOLING_K = 0.03   # °C/h per ΔT — conservative estimate


class ThermalModel:
    """Predicts indoor and DHW temperatures. Self-improving via daily retraining."""

    def __init__(self) -> None:
        self._observations: list[dict] = []
        self._model: Optional[LinearRegression | GradientBoostingRegressor] = None
        self._scaler = StandardScaler()
        self._is_gbr = False
        self._params = ThermalModelParams()
        self._dhw_k = _DEFAULT_DHW_COOLING_K
        self._log = get_logger("thermal_model")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        """True when enough observations to trust predictions."""
        return len(self._observations) >= MIN_SAMPLES and self._model is not None

    def add_observation(self, obs: dict) -> None:
        """Add a training observation. Keys: outdoor_temp_c, solar_radiation_w_m2,
        wind_speed_ms, hour_of_day, hvac_active (bool), delta_indoor_c_per_hour."""
        self._observations.append(obs)

    def predict_temperature(
        self,
        current_indoor_c: float,
        outdoor_forecast: list[float],
        solar_forecast: list[float],
        wind_forecast: list[float],
        hvac_plan: list[bool],
    ) -> list[float]:
        """Predict indoor temp for next N hours (N = len(outdoor_forecast))."""
        n_hours = min(len(outdoor_forecast), len(hvac_plan))
        temps = [current_indoor_c]
        for i in range(n_hours):
            outdoor = outdoor_forecast[i] if i < len(outdoor_forecast) else outdoor_forecast[-1]
            solar = solar_forecast[i] if i < len(solar_forecast) else 0.0
            wind = wind_forecast[i] if i < len(wind_forecast) else 3.0
            hvac = hvac_plan[i] if i < len(hvac_plan) else False
            hour = (datetime.now().hour + i) % 24

            delta = self._predict_delta(temps[-1], outdoor, solar, wind, hvac, hour)
            temps.append(temps[-1] + delta)
        return temps[1:]  # Return only future hours

    def predict_dhw_temperature(
        self,
        current_dhw_c: float,
        outdoor_temp_c: float,
        hours_ahead: int,
    ) -> float:
        """Predict DHW tank temperature N hours from now (Newton's cooling law)."""
        delta = current_dhw_c - outdoor_temp_c
        return outdoor_temp_c + delta * math.exp(-self._dhw_k * hours_ahead)

    def should_preheat(
        self,
        target_temp_c: float,
        current_temp_c: float,
        desired_time: datetime,
        outdoor_forecast: list[float],
        solar_forecast: list[float],
        wind_forecast: list[float],
    ) -> datetime:
        """Return when to start heating to reach target_temp_c by desired_time."""
        temp_diff = target_temp_c - current_temp_c
        if temp_diff <= 0:
            return desired_time  # Already warm enough

        hours_to_desired = max(0.0, (desired_time - datetime.now()).total_seconds() / 3600)
        heating_rate = self._params.heating_rate_c_per_hour

        # Simple estimate: time needed = temp_diff / heating_rate
        hours_needed = temp_diff / max(0.05, heating_rate)
        start_offset = max(0.0, hours_to_desired - hours_needed)
        return datetime.now() + timedelta(hours=start_offset)

    def update_model(self, observations: list[dict]) -> ThermalModelParams:
        """Retrain on all accumulated observations. Called daily at 02:00."""
        if observations:
            self._observations = observations
        if len(self._observations) < MIN_SAMPLES:
            self._log.info(
                "thermal_model_not_ready",
                samples=len(self._observations),
                needed=MIN_SAMPLES,
            )
            return self._params

        X, y = self._build_dataset()
        if len(X) < 10:
            return self._params

        X_scaled = self._scaler.fit_transform(X)
        model = GradientBoostingRegressor(
            n_estimators=100, max_depth=4, random_state=42
        ) if self._is_gbr else LinearRegression()
        model.fit(X_scaled, y)
        self._model = model

        # Compute R² and update params
        r2 = float(model.score(X_scaled, y))
        cooling_rate = float(abs(np.mean([o["delta_indoor_c_per_hour"] for o in self._observations
                                          if not o.get("hvac_active", False)
                                          and o.get("outdoor_temp_c", 20) < o.get("indoor_temp_c", 20)]
                                         or [self._params.cooling_rate_c_per_hour])))
        heating_rate = float(abs(np.mean([o["delta_indoor_c_per_hour"] for o in self._observations
                                          if o.get("hvac_active", False)]
                                         or [self._params.heating_rate_c_per_hour])))

        self._params = ThermalModelParams(
            cooling_rate_c_per_hour=max(0.01, cooling_rate),
            heating_rate_c_per_hour=max(0.01, heating_rate),
            r2_score=r2,
            samples_count=len(self._observations),
            is_trained=True,
            model_type="gradient_boosting" if self._is_gbr else "linear",
        )
        self._log.info(
            "thermal_model_trained",
            samples=len(self._observations),
            r2=round(r2, 3),
            model=self._params.model_type,
        )
        return self._params

    def evaluate_upgrade(self) -> bool:
        """Return True and upgrade to GBR if R² is below threshold."""
        if self._is_gbr:
            return False  # Already upgraded
        if not self.is_ready():
            return False
        if self._params.r2_score < R2_UPGRADE_THRESHOLD:
            self._is_gbr = True
            self._log.info(
                "thermal_model_upgrade_gbr",
                r2=self._params.r2_score,
                threshold=R2_UPGRADE_THRESHOLD,
            )
            return True
        return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _predict_delta(
        self,
        indoor: float,
        outdoor: float,
        solar: float,
        wind: float,
        hvac: bool,
        hour: int,
    ) -> float:
        """Predict delta indoor temp for one hour."""
        if not self.is_ready():
            # Physics-based fallback
            if hvac:
                return self._params.heating_rate_c_per_hour
            heat_loss = (outdoor - indoor) * 0.1 + solar * 0.002
            return -self._params.cooling_rate_c_per_hour + heat_loss
        features = np.array([[outdoor, solar, wind, hour, float(hvac)]])
        features_scaled = self._scaler.transform(features)
        return float(self._model.predict(features_scaled)[0])

    def _build_dataset(self) -> tuple[np.ndarray, np.ndarray]:
        X, y = [], []
        for obs in self._observations:
            if "delta_indoor_c_per_hour" not in obs:
                continue
            X.append([
                obs.get("outdoor_temp_c", 10.0),
                obs.get("solar_radiation_w_m2", 0.0),
                obs.get("wind_speed_ms", 3.0),
                obs.get("hour_of_day", 12),
                float(obs.get("hvac_active", False)),
            ])
            y.append(obs["delta_indoor_c_per_hour"])
        return np.array(X), np.array(y)
