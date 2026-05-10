"""PVForecaster — calibrated PV production forecast via Ridge Regression.

Physical base model: GHI shortwave radiation * panel specs.
Ridge calibration: learns correction factor from actual vs predicted daily kWh.
Minimum 30 days of data before calibration is active.

Panel specs (GoodWe GW5K-ET, 18 panels):
  PANEL_KWP = 7.47  (nominal)
  PANEL_AREA_M2 = 18 * 1.722 = 30.996 m²
  BASE_EFFICIENCY = 0.80 (incl. inverter + cable losses)
  PANEL_TILT_DEG = 35°, PANEL_AZIMUTH_DEG = 180° (south)

Ridge calibration features (5):
  cloud_cover_pct, temperature_c, wind_speed_ms, hour_of_day, day_of_year
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Optional

import numpy as np
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from energybrain.models import HourlyForecast, SurplusWindow, WeatherForecast
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

PANEL_KWP = 7.47
PANEL_AREA_M2 = 18 * 1.722
BASE_EFFICIENCY = 0.80
PANEL_TILT_DEG = 35
MIN_CALIBRATION_DAYS = 30


def _physical_pv_w(shortwave_w_m2: float) -> float:
    """Physical base estimate: GHI → PV output in watts."""
    return max(0.0, shortwave_w_m2 * PANEL_KWP * BASE_EFFICIENCY)


class PVForecaster:
    """Applies Ridge Regression calibration to WeatherAgent PV estimates."""

    def __init__(self) -> None:
        self._ridge: Optional[Ridge] = None
        self._scaler = StandardScaler()
        self._calibration_observations: list[dict] = []
        self._log = get_logger("pv_forecaster")

    def is_calibrated(self) -> bool:
        """True when MIN_CALIBRATION_DAYS of actual vs predicted data collected."""
        return len(self._calibration_observations) >= MIN_CALIBRATION_DAYS and self._ridge is not None

    def forecast(self, weather: WeatherForecast) -> WeatherForecast:
        """Return calibrated WeatherForecast. Returns input unchanged if not calibrated."""
        if not self.is_calibrated():
            return weather

        calibrated = []
        for h in weather.hourly:
            calib_factor = self._calibration_factor(h)
            calibrated.append(HourlyForecast(
                hour=h.hour,
                pv_estimated_w=max(0.0, h.pv_estimated_w * calib_factor),
                cloud_cover_pct=h.cloud_cover_pct,
                temperature_c=h.temperature_c,
                is_surplus_window=h.is_surplus_window,
            ))

        daily_pv_kwh = sum(h.pv_estimated_w for h in calibrated[:24]) / 1000.0
        return WeatherForecast(
            location=weather.location,
            daily_pv_kwh=daily_pv_kwh,
            hourly=calibrated,
            pv_calibration_factor=float(np.mean([
                self._calibration_factor(h) for h in weather.hourly[:24]
            ])),
        )

    def identify_surplus_windows(
        self,
        hourly: list[HourlyForecast],
        min_surplus_w: float,
        grid_consumption_w: float = 500.0,
    ) -> list[SurplusWindow]:
        """Find continuous time blocks where estimated surplus exceeds threshold.

        Args:
            hourly: List of HourlyForecast (uses pv_estimated_w).
            min_surplus_w: Minimum net surplus to qualify as a surplus window.
            grid_consumption_w: Estimated baseline house consumption (W).
        """
        windows: list[SurplusWindow] = []
        start = None
        surplus_list: list[float] = []

        for h in hourly:
            surplus = h.pv_estimated_w - grid_consumption_w
            if surplus >= min_surplus_w:
                if start is None:
                    start = h.hour
                surplus_list.append(surplus)
            else:
                if start is not None and surplus_list:
                    end = h.hour
                    kwh = sum(surplus_list) / 1000.0
                    windows.append(SurplusWindow(
                        start_hour=start,
                        end_hour=end,
                        avg_surplus_w=float(np.mean(surplus_list)),
                        total_energy_kwh=kwh,
                    ))
                    start = None
                    surplus_list = []

        # Close any open window at end of list
        if start is not None and surplus_list:
            end = (hourly[-1].hour + 1) % 24 if hourly else 0
            windows.append(SurplusWindow(
                start_hour=start,
                end_hour=end,
                avg_surplus_w=float(np.mean(surplus_list)),
                total_energy_kwh=sum(surplus_list) / 1000.0,
            ))
        return windows

    def update_calibration(
        self,
        date: datetime,
        predicted_kwh: float,
        actual_kwh: float,
        avg_cloud_cover: float,
        avg_temp_c: float,
        avg_wind_ms: float,
    ) -> None:
        """Add daily actual vs predicted observation and retrain Ridge model."""
        if predicted_kwh <= 0.01:
            return  # Skip days with essentially no sun

        correction_factor = actual_kwh / predicted_kwh
        day_of_year = date.timetuple().tm_yday

        self._calibration_observations.append({
            "cloud_cover_pct": avg_cloud_cover,
            "temperature_c": avg_temp_c,
            "wind_speed_ms": avg_wind_ms,
            "day_of_year": day_of_year,
            "correction_factor": correction_factor,
        })

        if len(self._calibration_observations) >= MIN_CALIBRATION_DAYS:
            self._fit_ridge()

        self._log.info(
            "pv_calibration_updated",
            days=len(self._calibration_observations),
            correction_factor=round(correction_factor, 3),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _calibration_factor(self, h: HourlyForecast) -> float:
        """Predict calibration factor for a single HourlyForecast."""
        if self._ridge is None:
            return 1.0
        day_of_year = datetime.now().timetuple().tm_yday
        features = np.array([[
            h.cloud_cover_pct,
            h.temperature_c,
            3.0,            # wind speed unknown per-hour, use mean
            h.hour,
            day_of_year,
        ]])
        scaled = self._scaler.transform(features)
        factor = float(self._ridge.predict(scaled)[0])
        return max(0.1, min(2.0, factor))  # Clamp to reasonable range

    def _fit_ridge(self) -> None:
        X = np.array([[
            o["cloud_cover_pct"],
            o["temperature_c"],
            o["wind_speed_ms"],
            o.get("hour_of_day", 12),
            o["day_of_year"],
        ] for o in self._calibration_observations])
        y = np.array([o["correction_factor"] for o in self._calibration_observations])
        X_scaled = self._scaler.fit_transform(X)
        self._ridge = Ridge(alpha=1.0)
        self._ridge.fit(X_scaled, y)
        self._log.info("pv_ridge_trained", samples=len(self._calibration_observations))
