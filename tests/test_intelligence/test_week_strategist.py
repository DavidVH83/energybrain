"""Tests for energybrain.intelligence.week_strategist."""
from unittest.mock import MagicMock

import pytest

from energybrain.intelligence.oscillation_detector import OscillationDetector
from energybrain.intelligence.week_strategist import (
    COLD_THRESHOLD_C,
    COOLING_ENABLED,
    HEATING_WORTHWHILE_SURPLUS_KWH,
    WeekStrategist,
)
from energybrain.models import WeekStrategy


def _make_od(frozen: bool = False) -> OscillationDetector:
    od = MagicMock(spec=OscillationDetector)
    od.is_frozen.return_value = frozen
    return od


def _make_thermal_model(ready: bool = False, min_pred: float = 18.0):
    model = MagicMock()
    model.is_ready.return_value = ready
    model.predict_temperature.return_value = [min_pred] * 8
    return model


def _make_forecaster():
    return MagicMock()


def _week_forecast(n: int = 7, avg_outdoor: float = 15.0, min_outdoor: float = 10.0,
                   daily_pv_kwh: float = 8.0) -> list[dict]:
    return [
        {"day_index": i, "avg_outdoor_c": avg_outdoor, "min_outdoor_c": min_outdoor,
         "daily_pv_kwh": daily_pv_kwh}
        for i in range(n)
    ]


class TestCalculateStrategy:
    def test_returns_week_strategy(self):
        ws = WeekStrategist()
        strategy = ws.calculate_strategy(
            _make_thermal_model(), _make_forecaster(), _make_od(),
            _week_forecast()
        )
        assert isinstance(strategy, WeekStrategy)

    def test_neutral_when_oscillation_frozen(self):
        ws = WeekStrategist()
        strategy = ws.calculate_strategy(
            _make_thermal_model(), _make_forecaster(), _make_od(frozen=True),
            _week_forecast()
        )
        assert strategy.oscillation_risk is True
        assert strategy.heating_days == []
        assert strategy.cooling_days == []
        assert len(strategy.neutral_days) == 7

    def test_neutral_when_no_forecast(self):
        ws = WeekStrategist()
        strategy = ws.calculate_strategy(
            _make_thermal_model(), _make_forecaster(), _make_od(),
            None
        )
        assert strategy.heating_days == []
        assert strategy.cooling_days == []

    def test_heating_on_cold_days_with_pv(self):
        ws = WeekStrategist()
        forecast = _week_forecast(
            avg_outdoor=3.0,
            min_outdoor=0.0,
            daily_pv_kwh=HEATING_WORTHWHILE_SURPLUS_KWH + 1.0,
        )
        # With untrained model: fallback is avg_outdoor < 10 → True
        strategy = ws.calculate_strategy(
            _make_thermal_model(ready=False),
            _make_forecaster(),
            _make_od(),
            forecast,
        )
        assert len(strategy.heating_days) > 0

    def test_neutral_on_mild_days(self):
        ws = WeekStrategist()
        forecast = _week_forecast(avg_outdoor=15.0, min_outdoor=12.0, daily_pv_kwh=5.0)
        strategy = ws.calculate_strategy(
            _make_thermal_model(), _make_forecaster(), _make_od(),
            forecast,
        )
        assert len(strategy.neutral_days) == 7
        assert len(strategy.heating_days) == 0

    def test_cooling_always_disabled(self):
        ws = WeekStrategist()
        # Hot day
        forecast = _week_forecast(avg_outdoor=28.0, min_outdoor=22.0, daily_pv_kwh=20.0)
        strategy = ws.calculate_strategy(
            _make_thermal_model(), _make_forecaster(), _make_od(),
            forecast,
        )
        assert strategy.cooling_days == []

    def test_all_days_accounted_for(self):
        ws = WeekStrategist()
        forecast = _week_forecast()
        strategy = ws.calculate_strategy(
            _make_thermal_model(), _make_forecaster(), _make_od(),
            forecast,
        )
        all_days = set(strategy.heating_days) | set(strategy.cooling_days) | set(strategy.neutral_days)
        assert all_days == set(range(7))

    def test_heating_when_thermal_model_predicts_cold(self):
        ws = WeekStrategist()
        # Trained model predicts house drops to 16°C (below 17.5 threshold)
        model = _make_thermal_model(ready=True, min_pred=16.0)
        forecast = _week_forecast(
            avg_outdoor=5.0, min_outdoor=1.0,
            daily_pv_kwh=HEATING_WORTHWHILE_SURPLUS_KWH + 1.0
        )
        strategy = ws.calculate_strategy(model, _make_forecaster(), _make_od(), forecast)
        assert len(strategy.heating_days) > 0

    def test_neutral_when_thermal_model_says_house_stays_warm(self):
        ws = WeekStrategist()
        # Trained model predicts house stays at 19°C — no heating needed
        model = _make_thermal_model(ready=True, min_pred=19.0)
        forecast = _week_forecast(
            avg_outdoor=8.0, min_outdoor=COLD_THRESHOLD_C - 1.0,
            daily_pv_kwh=HEATING_WORTHWHILE_SURPLUS_KWH + 1.0
        )
        strategy = ws.calculate_strategy(model, _make_forecaster(), _make_od(), forecast)
        # House stays warm → should be neutral
        assert len(strategy.neutral_days) == 7

    def test_heating_without_pv_still_heats(self):
        ws = WeekStrategist()
        # Cold but very little PV
        forecast = _week_forecast(
            avg_outdoor=2.0, min_outdoor=-1.0,
            daily_pv_kwh=0.5,
        )
        strategy = ws.calculate_strategy(
            _make_thermal_model(ready=False),
            _make_forecaster(),
            _make_od(),
            forecast,
        )
        # Still heats because it's cold (note from spec: even without PV, heat if cold)
        assert len(strategy.heating_days) > 0

    def test_reasoning_not_empty(self):
        ws = WeekStrategist()
        forecast = _week_forecast(avg_outdoor=3.0, min_outdoor=0.0, daily_pv_kwh=8.0)
        strategy = ws.calculate_strategy(
            _make_thermal_model(), _make_forecaster(), _make_od(), forecast
        )
        assert isinstance(strategy.reasoning, str)


class TestExplainStrategy:
    def test_returns_string(self):
        ws = WeekStrategist()
        strategy = WeekStrategy(
            heating_days=[0, 1],
            cooling_days=[],
            neutral_days=[2, 3, 4, 5, 6],
        )
        explanation = ws.explain_strategy(strategy)
        assert isinstance(explanation, str)

    def test_mentions_heating_days(self):
        ws = WeekStrategist()
        strategy = WeekStrategy(heating_days=[0], cooling_days=[], neutral_days=list(range(1, 7)))
        explanation = ws.explain_strategy(strategy)
        assert "voorladen" in explanation or "Koud" in explanation

    def test_mentions_neutral_days(self):
        ws = WeekStrategist()
        strategy = WeekStrategy(heating_days=[], cooling_days=[], neutral_days=list(range(7)))
        explanation = ws.explain_strategy(strategy)
        assert "geen actie" in explanation.lower() or "Stabiel" in explanation

    def test_oscillation_warning_in_explanation(self):
        ws = WeekStrategist()
        strategy = WeekStrategy(
            heating_days=[], cooling_days=[], neutral_days=list(range(7)),
            oscillation_risk=True
        )
        explanation = ws.explain_strategy(strategy)
        assert "Oscillatie" in explanation or "oscillatie" in explanation

    def test_covers_all_7_days(self):
        ws = WeekStrategist()
        strategy = WeekStrategy(
            heating_days=[0, 1],
            cooling_days=[],
            neutral_days=[2, 3, 4, 5, 6],
        )
        explanation = ws.explain_strategy(strategy)
        day_names = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]
        for name in day_names:
            assert name in explanation


class TestHouseNeedsHeating:
    def test_fallback_below_10c_needs_heating(self):
        model = _make_thermal_model(ready=False)
        assert WeekStrategist._house_needs_heating(8.0, model) is True

    def test_fallback_above_10c_no_heating(self):
        model = _make_thermal_model(ready=False)
        assert WeekStrategist._house_needs_heating(12.0, model) is False

    def test_trained_model_cold_prediction_needs_heating(self):
        model = _make_thermal_model(ready=True, min_pred=16.0)
        assert WeekStrategist._house_needs_heating(8.0, model) is True

    def test_trained_model_warm_prediction_no_heating(self):
        model = _make_thermal_model(ready=True, min_pred=19.0)
        assert WeekStrategist._house_needs_heating(8.0, model) is False
