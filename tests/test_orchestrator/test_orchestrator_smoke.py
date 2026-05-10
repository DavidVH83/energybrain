"""Smoke tests for energybrain.orchestrator.orchestrator.

These tests verify construction and pure helper methods only — no async loops
are started, no HA connections are made.
"""
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from energybrain.config import load_config
from energybrain.models import (
    BatteryDispatchPlan,
    DayPlan,
    HourlyForecast,
    WeatherForecast,
    WeekStrategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config():
    return load_config()


def _make_db():
    db = MagicMock()
    db._conn = None
    return db


def _make_orchestrator():
    from energybrain.orchestrator.orchestrator import Orchestrator
    with patch("energybrain.orchestrator.orchestrator.HAClient"):
        return Orchestrator(_make_config(), _make_db())


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruction:
    def test_can_construct(self):
        orch = _make_orchestrator()
        assert orch is not None

    def test_initial_state_is_none(self):
        orch = _make_orchestrator()
        assert orch._current_state is None

    def test_day_plan_initially_none(self):
        orch = _make_orchestrator()
        assert orch._day_plan is None

    def test_week_strategy_initially_none(self):
        orch = _make_orchestrator()
        assert orch._week_strategy is None

    def test_scheduled_done_empty(self):
        orch = _make_orchestrator()
        assert orch._scheduled_done == {}


# ---------------------------------------------------------------------------
# _expand_to_96
# ---------------------------------------------------------------------------

def _hourly_forecasts(pv_values: list) -> list:
    return [
        HourlyForecast(hour=i, pv_estimated_w=float(v), cloud_cover_pct=20.0, temperature_c=15.0)
        for i, v in enumerate(pv_values)
    ]


class TestExpandTo96:
    def test_expands_24_to_96(self):
        orch = _make_orchestrator()
        hourly = _hourly_forecasts(range(24))
        result = orch._expand_to_96(hourly)
        assert len(result) == 96

    def test_each_hour_repeated_4_times(self):
        orch = _make_orchestrator()
        hourly = _hourly_forecasts([100.0] * 24)
        result = orch._expand_to_96(hourly)
        assert all(v == 100.0 for v in result)

    def test_correct_values(self):
        orch = _make_orchestrator()
        hourly = _hourly_forecasts(range(24))
        result = orch._expand_to_96(hourly)
        # Hour 0 → slots 0-3
        assert result[0] == result[1] == result[2] == result[3] == 0.0
        # Hour 1 → slots 4-7
        assert result[4] == 1.0
        # Hour 23 → slots 92-95
        assert result[92] == 23.0

    def test_empty_input_returns_96_zeros(self):
        orch = _make_orchestrator()
        result = orch._expand_to_96([])
        assert len(result) == 96
        assert all(v == 0.0 for v in result)


# ---------------------------------------------------------------------------
# _build_week_forecast
# ---------------------------------------------------------------------------

class TestBuildWeekForecast:
    def _make_state_with_weather(self, n_hours: int = 24) -> MagicMock:
        from energybrain.models import (
            ApplianceType, BatteryMode, BatteryState, DeviceStatus,
            EnergyPrice, GridState, HeatPumpState, HVACMode, PVState, SystemState,
            ApplianceState,
        )
        hourly = [
            HourlyForecast(hour=h % 24, pv_estimated_w=float(h * 50),
                           cloud_cover_pct=20.0, temperature_c=15.0)
            for h in range(n_hours)
        ]
        weather = WeatherForecast(location="Test", daily_pv_kwh=10.0, hourly=hourly)
        state = MagicMock()
        state.weather = weather
        return state

    def test_returns_7_day_forecast(self):
        orch = _make_orchestrator()
        state = self._make_state_with_weather(168)
        result = orch._build_week_forecast(state)
        assert len(result) == 7

    def test_each_entry_has_required_keys(self):
        orch = _make_orchestrator()
        state = self._make_state_with_weather(168)
        result = orch._build_week_forecast(state)
        for entry in result:
            assert "day_index" in entry
            assert "avg_outdoor_c" in entry
            assert "min_outdoor_c" in entry
            assert "daily_pv_kwh" in entry

    def test_returns_empty_when_no_forecast(self):
        orch = _make_orchestrator()
        state = MagicMock()
        state.weather = None
        result = orch._build_week_forecast(state)
        assert result == []

    def test_day_index_sequence(self):
        orch = _make_orchestrator()
        state = self._make_state_with_weather(168)
        result = orch._build_week_forecast(state)
        assert [e["day_index"] for e in result] == list(range(7))


# ---------------------------------------------------------------------------
# _get_state
# ---------------------------------------------------------------------------

class TestGetState:
    def test_returns_current_state(self):
        orch = _make_orchestrator()
        mock_state = MagicMock()
        orch._current_state = mock_state
        assert orch._get_state() is mock_state

    def test_returns_none_when_unset(self):
        orch = _make_orchestrator()
        assert orch._get_state() is None


# ---------------------------------------------------------------------------
# _job_once deduplication
# ---------------------------------------------------------------------------

class TestJobOnce:
    @pytest.mark.asyncio
    async def test_runs_job_first_time(self):
        orch = _make_orchestrator()
        called = []

        async def my_job():
            called.append(1)

        await orch._job_once("test_job_2025-01-15", my_job)
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_skips_job_when_already_done(self):
        orch = _make_orchestrator()
        orch._scheduled_done["test_job_2025-01-15"] = "some_timestamp"
        called = []

        async def my_job():
            called.append(1)

        await orch._job_once("test_job_2025-01-15", my_job)
        assert len(called) == 0

    @pytest.mark.asyncio
    async def test_runs_again_on_different_date(self):
        orch = _make_orchestrator()
        orch._scheduled_done["test_job_2025-01-14"] = "some_timestamp"
        called = []

        async def my_job():
            called.append(1)

        await orch._job_once("test_job_2025-01-15", my_job)
        assert len(called) == 1
