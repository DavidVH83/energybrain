"""Integration tests — week strategy scenarios.

Tests WeekStrategist across three realistic scenarios:
  - Oscillation scenario: oscillation detected, strategy frozen
  - Cold period: heating scheduled when PV available
  - Stable mild week: all neutral
All run offline (no real HA required).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from energybrain.intelligence.oscillation_detector import OscillationDetector
from energybrain.intelligence.thermal_model import ThermalModel
from energybrain.intelligence.week_strategist import (
    COOLING_ENABLED,
    HEATING_WORTHWHILE_SURPLUS_KWH,
    WeekStrategist,
)
from energybrain.models import WeekStrategy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _week_forecast(
    avg_outdoor: float = 15.0,
    min_outdoor: float = 10.0,
    daily_pv_kwh: float = 8.0,
    n: int = 7,
) -> list[dict]:
    return [
        {
            "day_index": i,
            "avg_outdoor_c": avg_outdoor,
            "min_outdoor_c": min_outdoor,
            "daily_pv_kwh": daily_pv_kwh,
        }
        for i in range(n)
    ]


def _untrained_thermal():
    model = MagicMock()
    model.is_ready.return_value = False
    return model


def _trained_thermal(min_predicted_c: float = 18.0):
    model = MagicMock()
    model.is_ready.return_value = True
    model.predict_temperature.return_value = [min_predicted_c] * 8
    return model


def _oscillation_detector(frozen: bool = False) -> OscillationDetector:
    od = MagicMock(spec=OscillationDetector)
    od.is_frozen.return_value = frozen
    return od


# ---------------------------------------------------------------------------
# Oscillation scenario — strategy frozen
# ---------------------------------------------------------------------------

class TestOscillationScenario:
    def test_oscillation_frozen_returns_neutral_strategy(self):
        ws = WeekStrategist()
        od = _oscillation_detector(frozen=True)
        strategy = ws.calculate_strategy(
            _untrained_thermal(), None, od, _week_forecast()
        )
        assert strategy.oscillation_risk is True
        assert strategy.heating_days == []
        assert strategy.cooling_days == []
        assert len(strategy.neutral_days) == 7

    def test_oscillation_explanation_mentions_risk(self):
        ws = WeekStrategist()
        strategy = WeekStrategy(
            heating_days=[], cooling_days=[], neutral_days=list(range(7)),
            oscillation_risk=True,
        )
        explanation = ws.explain_strategy(strategy)
        assert "oscillatie" in explanation.lower() or "Oscillatie" in explanation

    def test_oscillation_detector_detects_rapid_switching(self):
        """OscillationDetector should freeze after ≥3 HVAC mode switches with temp swing."""
        od = OscillationDetector()

        # history must be list of dicts with "mode" key
        history = [
            {"mode": "heat"}, {"mode": "off"}, {"mode": "heat"},
            {"mode": "off"}, {"mode": "heat"}, {"mode": "off"},
        ]
        outdoor_temps = [5.0, 15.0, 4.0, 16.0, 5.0, 14.0]  # >8°C swing
        od.check(history, outdoor_temps)
        assert od.is_frozen() is True

    def test_oscillation_unfreezes_after_48h(self):
        od = OscillationDetector()

        history = [
            {"mode": "heat"}, {"mode": "off"}, {"mode": "heat"}, {"mode": "off"},
        ]
        outdoor_temps = [3.0, 15.0, 2.0, 16.0]
        od.check(history, outdoor_temps)

        # Simulate freeze has expired
        if od.is_frozen():
            od._freeze_until = datetime.now() - timedelta(hours=1)
        assert od.is_frozen() is False


# ---------------------------------------------------------------------------
# Cold period — heating with PV
# ---------------------------------------------------------------------------

class TestColdPeriodHeating:
    def test_heating_scheduled_on_cold_days_with_pv(self):
        ws = WeekStrategist()
        forecast = _week_forecast(
            avg_outdoor=2.0,
            min_outdoor=-1.0,
            daily_pv_kwh=HEATING_WORTHWHILE_SURPLUS_KWH + 2.0,
        )
        strategy = ws.calculate_strategy(
            _untrained_thermal(), None, _oscillation_detector(), forecast
        )
        assert len(strategy.heating_days) > 0

    def test_heating_still_on_cold_days_without_pv(self):
        """Spec: even without PV, heat if cold (resident comfort > savings)."""
        ws = WeekStrategist()
        forecast = _week_forecast(
            avg_outdoor=1.0,
            min_outdoor=-2.0,
            daily_pv_kwh=0.5,  # Almost no PV
        )
        strategy = ws.calculate_strategy(
            _untrained_thermal(), None, _oscillation_detector(), forecast
        )
        assert len(strategy.heating_days) > 0

    def test_trained_model_overrides_fallback_when_warm_house(self):
        """Trained model: house stays warm → no heating even on cold outdoor day."""
        ws = WeekStrategist()
        model = _trained_thermal(min_predicted_c=19.5)  # House stays at 19.5°C
        forecast = _week_forecast(
            avg_outdoor=5.0,
            min_outdoor=2.0,
            daily_pv_kwh=HEATING_WORTHWHILE_SURPLUS_KWH + 1.0,
        )
        strategy = ws.calculate_strategy(model, None, _oscillation_detector(), forecast)
        assert len(strategy.neutral_days) == 7

    def test_trained_model_heats_when_house_drops_below_comfort(self):
        ws = WeekStrategist()
        model = _trained_thermal(min_predicted_c=16.0)  # House drops to 16°C
        forecast = _week_forecast(
            avg_outdoor=3.0,
            min_outdoor=0.0,
            daily_pv_kwh=HEATING_WORTHWHILE_SURPLUS_KWH + 1.0,
        )
        strategy = ws.calculate_strategy(model, None, _oscillation_detector(), forecast)
        assert len(strategy.heating_days) > 0

    def test_all_7_days_accounted_for_in_cold_strategy(self):
        ws = WeekStrategist()
        forecast = _week_forecast(avg_outdoor=2.0, min_outdoor=-1.0, daily_pv_kwh=10.0)
        strategy = ws.calculate_strategy(
            _untrained_thermal(), None, _oscillation_detector(), forecast
        )
        all_days = set(strategy.heating_days) | set(strategy.cooling_days) | set(strategy.neutral_days)
        assert all_days == set(range(7))


# ---------------------------------------------------------------------------
# Stable mild week — all neutral
# ---------------------------------------------------------------------------

class TestStableMildWeek:
    def test_all_days_neutral_on_mild_week(self):
        ws = WeekStrategist()
        forecast = _week_forecast(avg_outdoor=16.0, min_outdoor=13.0, daily_pv_kwh=5.0)
        strategy = ws.calculate_strategy(
            _untrained_thermal(), None, _oscillation_detector(), forecast
        )
        assert len(strategy.neutral_days) == 7

    def test_no_cooling_even_on_hot_week(self):
        """COOLING_ENABLED=False — never schedule cooling days."""
        assert COOLING_ENABLED is False
        ws = WeekStrategist()
        forecast = _week_forecast(avg_outdoor=30.0, min_outdoor=22.0, daily_pv_kwh=25.0)
        strategy = ws.calculate_strategy(
            _untrained_thermal(), None, _oscillation_detector(), forecast
        )
        assert strategy.cooling_days == []

    def test_explanation_covers_all_7_days(self):
        ws = WeekStrategist()
        strategy = WeekStrategy(
            heating_days=[],
            cooling_days=[],
            neutral_days=list(range(7)),
        )
        explanation = ws.explain_strategy(strategy)
        day_names = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]
        for name in day_names:
            assert name in explanation

    def test_neutral_explanation_mentions_stability(self):
        ws = WeekStrategist()
        strategy = WeekStrategy(heating_days=[], cooling_days=[], neutral_days=list(range(7)))
        explanation = ws.explain_strategy(strategy)
        assert "geen actie" in explanation.lower() or "Stabiel" in explanation or "stabiel" in explanation

    def test_empty_forecast_returns_neutral_strategy(self):
        ws = WeekStrategist()
        strategy = ws.calculate_strategy(
            _untrained_thermal(), None, _oscillation_detector(), None
        )
        assert strategy.heating_days == []
        assert strategy.cooling_days == []


# ---------------------------------------------------------------------------
# Mixed week — some cold, some mild
# ---------------------------------------------------------------------------

class TestMixedWeek:
    def test_mixed_week_some_heating_some_neutral(self):
        ws = WeekStrategist()
        forecast = [
            {"day_index": 0, "avg_outdoor_c": 1.0, "min_outdoor_c": -2.0, "daily_pv_kwh": 10.0},
            {"day_index": 1, "avg_outdoor_c": 3.0, "min_outdoor_c": 0.0, "daily_pv_kwh": 10.0},
            {"day_index": 2, "avg_outdoor_c": 15.0, "min_outdoor_c": 12.0, "daily_pv_kwh": 5.0},
            {"day_index": 3, "avg_outdoor_c": 16.0, "min_outdoor_c": 13.0, "daily_pv_kwh": 5.0},
            {"day_index": 4, "avg_outdoor_c": 2.0, "min_outdoor_c": -1.0, "daily_pv_kwh": 10.0},
            {"day_index": 5, "avg_outdoor_c": 14.0, "min_outdoor_c": 11.0, "daily_pv_kwh": 5.0},
            {"day_index": 6, "avg_outdoor_c": 14.0, "min_outdoor_c": 11.0, "daily_pv_kwh": 5.0},
        ]
        strategy = ws.calculate_strategy(
            _untrained_thermal(), None, _oscillation_detector(), forecast
        )
        # Cold days (0, 1, 4) should be heating; mild (2, 3, 5, 6) neutral
        assert len(strategy.heating_days) > 0
        assert len(strategy.neutral_days) > 0
        all_days = set(strategy.heating_days) | set(strategy.cooling_days) | set(strategy.neutral_days)
        assert all_days == set(range(7))
