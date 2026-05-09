"""Tests for energybrain.agents.weather_agent."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from energybrain.agents.weather_agent import WeatherAgent, _estimate_pv_w
from energybrain.config import load_config


def _make_open_meteo_response(n_hours: int = 168) -> dict:
    """Minimal Open-Meteo API response for testing."""
    times = [f"2026-05-0{1 + i // 24}T{i % 24:02d}:00" for i in range(n_hours)]
    return {
        "hourly": {
            "time": times,
            "shortwave_radiation": [float(i % 24 * 50) for i in range(n_hours)],
            "direct_radiation": [float(i % 24 * 40) for i in range(n_hours)],
            "diffuse_radiation": [float(i % 24 * 10) for i in range(n_hours)],
            "cloud_cover": [float(i % 100) for i in range(n_hours)],
            "temperature_2m": [15.0 + (i % 24) * 0.2 for i in range(n_hours)],
            "windspeed_10m": [5.0] * n_hours,
        }
    }


class TestEstimatePvW:
    def test_zero_irradiance_gives_zero(self):
        assert _estimate_pv_w(0.0) == pytest.approx(0.0)

    def test_1000_w_m2_gives_approx_system_output(self):
        # 5 kWp * 0.85 efficiency = 4.25 kW = 4250 W at STC
        result = _estimate_pv_w(1000.0)
        assert result == pytest.approx(4250.0)

    def test_calibration_factor_applied(self):
        result = _estimate_pv_w(1000.0, calibration_factor=0.9)
        assert result == pytest.approx(4250.0 * 0.9)

    def test_negative_irradiance_clamped_to_zero(self):
        assert _estimate_pv_w(-100.0) == pytest.approx(0.0)


class TestWeatherAgentBuildUrl:
    def test_url_contains_location(self, minimal_env):
        agent = WeatherAgent(load_config())
        url = agent._build_url()
        assert "50.8597" in url
        assert "4.7628" in url

    def test_url_contains_hourly_params(self, minimal_env):
        agent = WeatherAgent(load_config())
        url = agent._build_url()
        assert "shortwave_radiation" in url
        assert "cloud_cover" in url
        assert "temperature_2m" in url


class TestWeatherAgentParse:
    def test_parse_returns_168_hours(self, minimal_env):
        agent = WeatherAgent(load_config())
        data = _make_open_meteo_response(168)
        forecast = agent._parse(data)
        assert len(forecast.hourly) == 168

    def test_parse_truncates_to_168_hours(self, minimal_env):
        agent = WeatherAgent(load_config())
        data = _make_open_meteo_response(200)
        forecast = agent._parse(data)
        assert len(forecast.hourly) == 168

    def test_daily_pv_kwh_is_sum_of_first_24h(self, minimal_env):
        agent = WeatherAgent(load_config())
        data = _make_open_meteo_response(168)
        forecast = agent._parse(data)
        expected = sum(h.pv_estimated_w for h in forecast.hourly[:24]) / 1000.0
        assert forecast.daily_pv_kwh == pytest.approx(expected)

    def test_location_contains_coordinates(self, minimal_env):
        agent = WeatherAgent(load_config())
        data = _make_open_meteo_response(24)
        forecast = agent._parse(data)
        assert "50.8597" in forecast.location

    def test_calibration_factor_stored(self, minimal_env):
        agent = WeatherAgent(load_config())
        data = _make_open_meteo_response(24)
        forecast = agent._parse(data, calibration_factor=1.2)
        assert forecast.pv_calibration_factor == pytest.approx(1.2)


class TestWeatherAgentCache:
    async def test_collect_uses_cache_within_15_min(self, minimal_env):
        agent = WeatherAgent(load_config())
        data = _make_open_meteo_response()
        forecast = agent._parse(data)
        agent._cache = (datetime.now(), forecast)

        with patch.object(agent, "_fetch", new=AsyncMock()) as mock_fetch:
            result = await agent.collect()
            mock_fetch.assert_not_called()
        assert result is forecast

    async def test_collect_refetches_after_15_min(self, minimal_env):
        agent = WeatherAgent(load_config())
        stale_time = datetime.now() - timedelta(minutes=16)
        data = _make_open_meteo_response()
        stale_forecast = agent._parse(data)
        agent._cache = (stale_time, stale_forecast)

        fresh_data = _make_open_meteo_response()
        fresh_forecast = agent._parse(fresh_data, calibration_factor=1.1)

        with patch.object(agent, "_fetch", new=AsyncMock(return_value=fresh_forecast)) as mock_fetch:
            result = await agent.collect()
            mock_fetch.assert_awaited_once()
        assert result is fresh_forecast

    async def test_collect_fetches_when_no_cache(self, minimal_env):
        agent = WeatherAgent(load_config())
        data = _make_open_meteo_response()
        new_forecast = agent._parse(data)

        with patch.object(agent, "_fetch", new=AsyncMock(return_value=new_forecast)):
            result = await agent.collect()
        assert result is new_forecast
        assert agent._cache is not None
