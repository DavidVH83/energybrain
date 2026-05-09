"""WeatherAgent — fetches 7-day weather and PV forecast from Open-Meteo.

No API key required. 15-minute in-memory cache to avoid hammering the API.
PV estimation uses GHI (shortwave radiation) scaled by system size and efficiency.
PVForecaster (Fase 4) refines estimates via calibration_factor stored in DB.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Optional

import aiohttp

from energybrain.config import Config
from energybrain.models import HourlyForecast, WeatherForecast
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.open-meteo.com/v1/forecast"
_CACHE_SECONDS = 15 * 60       # 15 minutes
_SYSTEM_PEAK_KW = 5.0          # GoodWe GW5K-ET (5 kWp)
_SYSTEM_EFFICIENCY = 0.85      # Inverter + cable losses
_REQUEST_TIMEOUT_S = 15


def _estimate_pv_w(shortwave_w_m2: float, calibration_factor: float = 1.0) -> float:
    """Estimate PV output (W) from GHI shortwave radiation.

    At 1000 W/m² STC, a 5 kWp system produces 5000 W * 0.85 = 4250 W.
    calibration_factor is updated by PVForecaster from historical accuracy.
    """
    raw = shortwave_w_m2 * _SYSTEM_PEAK_KW * _SYSTEM_EFFICIENCY
    return max(0.0, raw * calibration_factor)


class WeatherAgent:
    """Fetches Open-Meteo forecast and converts to WeatherForecast model."""

    AGENT_NAME = "weather_agent"

    def __init__(self, config: Config) -> None:
        self._config = config
        self._cache: Optional[tuple[datetime, WeatherForecast]] = None
        self._log = get_logger(self.AGENT_NAME)

    def _build_url(self) -> str:
        lat = self._config.latitude
        lon = self._config.longitude
        return (
            f"{_API_BASE}"
            f"?latitude={lat}&longitude={lon}"
            "&hourly=shortwave_radiation,direct_radiation,diffuse_radiation"
            "&hourly=cloud_cover,temperature_2m,windspeed_10m"
            "&forecast_days=7&timezone=Europe%2FBrussels"
        )

    async def collect(self, calibration_factor: float = 1.0) -> WeatherForecast:
        """Return WeatherForecast, served from cache if younger than 15 min.

        Args:
            calibration_factor: Multiplier from PVForecaster (default 1.0).
        """
        if self._cache is not None:
            cached_at, cached = self._cache
            age_s = (datetime.now() - cached_at).total_seconds()
            if age_s < _CACHE_SECONDS:
                self._log.debug("weather_cache_hit", age_s=round(age_s))
                return cached

        forecast = await self._fetch(calibration_factor)
        self._cache = (datetime.now(), forecast)
        self._log.info(
            "weather_fetched",
            daily_pv_kwh=round(forecast.daily_pv_kwh, 2),
            hours=len(forecast.hourly),
        )
        return forecast

    async def _fetch(self, calibration_factor: float = 1.0) -> WeatherForecast:
        url = self._build_url()
        timeout = aiohttp.ClientTimeout(total=_REQUEST_TIMEOUT_S)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=timeout) as resp:
                resp.raise_for_status()
                data = await resp.json()
        return self._parse(data, calibration_factor)

    def _parse(self, data: dict, calibration_factor: float = 1.0) -> WeatherForecast:
        hourly = data.get("hourly", {})
        times: list[str] = hourly.get("time", [])
        shortwave: list = hourly.get("shortwave_radiation", [])
        cloud_cover: list = hourly.get("cloud_cover", [])
        temperature: list = hourly.get("temperature_2m", [])

        hourly_forecasts: list[HourlyForecast] = []
        for i, t in enumerate(times[:168]):  # 7 days = 168 hours
            sw = float(shortwave[i] or 0.0) if i < len(shortwave) else 0.0
            cc = float(cloud_cover[i] or 0.0) if i < len(cloud_cover) else 0.0
            temp = float(temperature[i] or 10.0) if i < len(temperature) else 10.0
            hour_of_day = int(t[11:13]) if len(t) >= 13 else 0

            hourly_forecasts.append(HourlyForecast(
                hour=hour_of_day,
                pv_estimated_w=_estimate_pv_w(sw, calibration_factor),
                cloud_cover_pct=cc,
                temperature_c=temp,
            ))

        daily_pv_kwh = sum(h.pv_estimated_w for h in hourly_forecasts[:24]) / 1000.0
        location = f"{self._config.latitude},{self._config.longitude}"

        return WeatherForecast(
            location=location,
            daily_pv_kwh=daily_pv_kwh,
            hourly=hourly_forecasts,
            pv_calibration_factor=calibration_factor,
        )
