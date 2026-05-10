"""Tests for energybrain.intelligence.pv_forecaster."""
from datetime import datetime

import pytest

from energybrain.intelligence.pv_forecaster import MIN_CALIBRATION_DAYS, PVForecaster
from energybrain.models import HourlyForecast, SurplusWindow, WeatherForecast


def _make_weather(pv_w: float = 1000.0, n_hours: int = 24) -> WeatherForecast:
    return WeatherForecast(
        location="test",
        daily_pv_kwh=pv_w * n_hours / 1000.0,
        hourly=[
            HourlyForecast(hour=h % 24, pv_estimated_w=pv_w, cloud_cover_pct=10.0, temperature_c=15.0)
            for h in range(n_hours)
        ],
    )


def _add_calibration_days(pv: PVForecaster, n: int = MIN_CALIBRATION_DAYS) -> None:
    for i in range(n):
        pv.update_calibration(
            date=datetime.now(),
            predicted_kwh=10.0,
            actual_kwh=10.5,
            avg_cloud_cover=20.0,
            avg_temp_c=15.0,
            avg_wind_ms=3.0,
        )


class TestIsCalibrated:
    def test_not_calibrated_initially(self):
        pv = PVForecaster()
        assert pv.is_calibrated() is False

    def test_not_calibrated_with_fewer_days(self):
        pv = PVForecaster()
        _add_calibration_days(pv, MIN_CALIBRATION_DAYS - 1)
        assert pv.is_calibrated() is False

    def test_calibrated_after_enough_days(self):
        pv = PVForecaster()
        _add_calibration_days(pv, MIN_CALIBRATION_DAYS)
        assert pv.is_calibrated() is True


class TestForecast:
    def test_returns_weather_unchanged_when_not_calibrated(self):
        pv = PVForecaster()
        weather = _make_weather(1000.0)
        result = pv.forecast(weather)
        assert result is weather

    def test_calibrated_forecast_returns_new_object(self):
        pv = PVForecaster()
        _add_calibration_days(pv)
        weather = _make_weather(1000.0)
        result = pv.forecast(weather)
        assert result is not weather

    def test_calibration_factor_stored_on_result(self):
        pv = PVForecaster()
        _add_calibration_days(pv)
        weather = _make_weather(1000.0)
        result = pv.forecast(weather)
        assert result.pv_calibration_factor > 0.0

    def test_calibrated_pv_values_non_negative(self):
        pv = PVForecaster()
        _add_calibration_days(pv)
        weather = _make_weather(500.0)
        result = pv.forecast(weather)
        assert all(h.pv_estimated_w >= 0.0 for h in result.hourly)

    def test_daily_pv_kwh_recalculated(self):
        pv = PVForecaster()
        _add_calibration_days(pv)
        weather = _make_weather(1000.0, n_hours=24)
        result = pv.forecast(weather)
        expected = sum(h.pv_estimated_w for h in result.hourly[:24]) / 1000.0
        assert result.daily_pv_kwh == pytest.approx(expected)

    def test_calibration_factor_clamped(self):
        pv = PVForecaster()
        # Add extreme corrections to push factor outside [0.1, 2.0]
        for _ in range(MIN_CALIBRATION_DAYS):
            pv.update_calibration(
                date=datetime.now(), predicted_kwh=1.0, actual_kwh=10.0,
                avg_cloud_cover=0.0, avg_temp_c=20.0, avg_wind_ms=1.0
            )
        weather = _make_weather(1000.0)
        result = pv.forecast(weather)
        assert all(h.pv_estimated_w <= 1000.0 * 2.0 for h in result.hourly)


class TestIdentifySurplusWindows:
    def test_no_windows_when_pv_below_threshold(self):
        pv = PVForecaster()
        hourly = [HourlyForecast(hour=h, pv_estimated_w=100.0, cloud_cover_pct=80.0, temperature_c=10.0)
                  for h in range(24)]
        windows = pv.identify_surplus_windows(hourly, min_surplus_w=1000.0)
        assert windows == []

    def test_window_detected_when_pv_exceeds_threshold(self):
        pv = PVForecaster()
        hourly = []
        for h in range(24):
            w = 3000.0 if 10 <= h <= 14 else 100.0
            hourly.append(HourlyForecast(hour=h, pv_estimated_w=w, cloud_cover_pct=10.0, temperature_c=15.0))
        windows = pv.identify_surplus_windows(hourly, min_surplus_w=1000.0, grid_consumption_w=500.0)
        assert len(windows) == 1
        assert windows[0].start_hour == 10

    def test_window_end_hour_correct(self):
        pv = PVForecaster()
        hourly = []
        for h in range(24):
            w = 3000.0 if 10 <= h <= 14 else 100.0
            hourly.append(HourlyForecast(hour=h, pv_estimated_w=w, cloud_cover_pct=10.0, temperature_c=15.0))
        windows = pv.identify_surplus_windows(hourly, min_surplus_w=1000.0, grid_consumption_w=500.0)
        assert windows[0].end_hour == 15  # First hour that drops below threshold

    def test_multiple_windows_detected(self):
        pv = PVForecaster()
        hourly = []
        for h in range(24):
            w = 3000.0 if h in (10, 11, 14, 15) else 100.0
            hourly.append(HourlyForecast(hour=h, pv_estimated_w=w, cloud_cover_pct=10.0, temperature_c=15.0))
        windows = pv.identify_surplus_windows(hourly, min_surplus_w=1000.0, grid_consumption_w=500.0)
        assert len(windows) == 2

    def test_open_window_closed_at_end_of_list(self):
        pv = PVForecaster()
        hourly = [
            HourlyForecast(hour=22, pv_estimated_w=3000.0, cloud_cover_pct=0.0, temperature_c=15.0),
            HourlyForecast(hour=23, pv_estimated_w=3000.0, cloud_cover_pct=0.0, temperature_c=15.0),
        ]
        windows = pv.identify_surplus_windows(hourly, min_surplus_w=1000.0, grid_consumption_w=500.0)
        assert len(windows) == 1

    def test_avg_surplus_calculated(self):
        pv = PVForecaster()
        hourly = [HourlyForecast(hour=h, pv_estimated_w=2500.0, cloud_cover_pct=10.0, temperature_c=15.0)
                  for h in range(3)]
        windows = pv.identify_surplus_windows(hourly, min_surplus_w=500.0, grid_consumption_w=500.0)
        if windows:
            assert windows[0].avg_surplus_w == pytest.approx(2000.0)


class TestUpdateCalibration:
    def test_skips_zero_predicted(self):
        pv = PVForecaster()
        pv.update_calibration(datetime.now(), 0.0, 5.0, 10.0, 15.0, 3.0)
        assert len(pv._calibration_observations) == 0

    def test_skips_near_zero_predicted(self):
        pv = PVForecaster()
        pv.update_calibration(datetime.now(), 0.005, 5.0, 10.0, 15.0, 3.0)
        assert len(pv._calibration_observations) == 0

    def test_observation_stored(self):
        pv = PVForecaster()
        pv.update_calibration(datetime.now(), 10.0, 12.0, 20.0, 15.0, 3.0)
        assert len(pv._calibration_observations) == 1

    def test_correction_factor_stored_correctly(self):
        pv = PVForecaster()
        pv.update_calibration(datetime.now(), 10.0, 12.0, 20.0, 15.0, 3.0)
        obs = pv._calibration_observations[0]
        assert obs["correction_factor"] == pytest.approx(1.2)

    def test_ridge_fitted_after_min_days(self):
        pv = PVForecaster()
        _add_calibration_days(pv, MIN_CALIBRATION_DAYS)
        assert pv._ridge is not None
