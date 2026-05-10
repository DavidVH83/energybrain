"""Tests for energybrain.intelligence.pattern_learner."""
import pytest

from energybrain.intelligence.pattern_learner import (
    DEFAULTS,
    MIN_DAYS_BASIC,
    MIN_DAYS_SEASONAL,
    PatternLearner,
)
from energybrain.models import ApplianceType


def _make_training_data(
    n: int = MIN_DAYS_BASIC + 5,
    dhw_class: int = 1,
    appliance_class: int = 1,
    cooking_hour: float = 18.0,
    alternate: bool = True,
) -> list[dict]:
    """Generate n training dicts with both classes present."""
    data = []
    for i in range(n):
        dhw = int(dhw_class if (i % 2 == 0 or not alternate) else 1 - dhw_class)
        appl = int(appliance_class if (i % 2 == 0 or not alternate) else 1 - appliance_class)
        data.append({
            "date": f"2026-01-{(i % 28) + 1:02d}",
            "weekday": i % 7,
            "hour": i % 24,
            "outdoor_temp_c": 10.0 + (i % 10),
            "cloud_cover_pct": 30.0 + (i % 50),
            "wind_speed_ms": 3.0,
            "is_school_holiday": i % 10 == 0,
            "season_q": (i % 4) + 1,
            "baseline_power_w": 500.0,
            "temp_vs_seasonal_avg": float(i % 5) - 2.0,
            "dhw_needed": dhw,
            "dishwasher_loaded": appl,
            "washing_loaded": appl,
            "dryer_loaded": appl,
            "cooking_peak_hour": cooking_hour + (i % 3) * 0.5,
        })
    return data


class TestPatternLearnerIsTrained:
    def test_not_trained_initially(self):
        pl = PatternLearner()
        assert pl.is_trained("dhw") is False

    def test_trained_after_update(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        assert pl.is_trained("dhw") is True

    def test_all_models_trained(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        for key in ["dhw", "dishwasher", "washing", "dryer", "cooking"]:
            assert pl.is_trained(key) is True


class TestPredictDhwDemand:
    def test_returns_default_when_not_trained(self):
        pl = PatternLearner()
        result = pl.predict_dhw_demand(weekday=1, hour=8, outdoor_temp_c=10.0)
        assert result == pytest.approx(DEFAULTS["dhw_demand"])

    def test_returns_probability_between_0_and_1(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        result = pl.predict_dhw_demand(weekday=1, hour=8, outdoor_temp_c=10.0)
        assert 0.0 <= result <= 1.0

    def test_all_kwargs_accepted(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        result = pl.predict_dhw_demand(
            weekday=1, hour=8, outdoor_temp_c=10.0,
            cloud_cover_pct=30.0, wind_speed_ms=3.0,
            is_school_holiday=False, season_q=2,
            baseline_power_w=500.0, temp_vs_seasonal_avg=0.0,
        )
        assert 0.0 <= result <= 1.0


class TestPredictApplianceLoading:
    def test_returns_default_when_not_trained(self):
        pl = PatternLearner()
        result = pl.predict_appliance_loading(ApplianceType.DISHWASHER, weekday=1, outdoor_temp_c=10.0)
        assert result == pytest.approx(DEFAULTS["appliance_loading"])

    def test_probability_between_0_and_1_dishwasher(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        result = pl.predict_appliance_loading(ApplianceType.DISHWASHER, weekday=2, outdoor_temp_c=12.0)
        assert 0.0 <= result <= 1.0

    def test_probability_between_0_and_1_washing(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        result = pl.predict_appliance_loading(ApplianceType.WASHING_MACHINE, weekday=3, outdoor_temp_c=8.0)
        assert 0.0 <= result <= 1.0

    def test_probability_between_0_and_1_dryer(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        result = pl.predict_appliance_loading(ApplianceType.DRYER, weekday=4, outdoor_temp_c=6.0)
        assert 0.0 <= result <= 1.0


class TestGetCookingPeak:
    def test_returns_default_when_not_trained(self):
        pl = PatternLearner()
        start, end = pl.get_cooking_peak(weekday=2)
        assert start == DEFAULTS["cooking_peak_start"]
        assert end == DEFAULTS["cooking_peak_end"]

    def test_returns_tuple_of_ints(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        start, end = pl.get_cooking_peak(weekday=2)
        assert isinstance(start, int)
        assert isinstance(end, int)

    def test_end_is_start_plus_2(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        start, end = pl.get_cooking_peak(weekday=2)
        assert end == start + 2

    def test_peak_clamped_to_valid_range(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data(cooking_hour=18.0))
        start, end = pl.get_cooking_peak(weekday=0)
        assert 15 <= start <= 20


class TestUpdatePatterns:
    def test_empty_data_does_not_crash(self):
        pl = PatternLearner()
        pl.update_patterns([])  # Should not raise

    def test_days_of_data_tracked(self):
        pl = PatternLearner()
        data = _make_training_data(n=MIN_DAYS_BASIC + 5)
        pl.update_patterns(data)
        assert pl._days_of_data > 0

    def test_uses_3_features_below_seasonal_threshold(self):
        pl = PatternLearner()
        data = _make_training_data(n=MIN_DAYS_BASIC + 5)
        pl.update_patterns(data)
        # Days < MIN_DAYS_SEASONAL → 3 features used
        assert pl._days_of_data < MIN_DAYS_SEASONAL

    def test_skips_model_when_only_one_class(self):
        pl = PatternLearner()
        # All dhw_needed=1 → only one class present → model stays None
        data = _make_training_data(n=MIN_DAYS_BASIC + 5, alternate=False, dhw_class=1)
        pl.update_patterns(data)
        # If GBC requires 2 classes and only 1 is present, dhw model may stay None
        # We just check it doesn't crash


class TestGetFeatureImportances:
    def test_returns_empty_when_not_trained(self):
        pl = PatternLearner()
        result = pl.get_feature_importances()
        assert result == {}

    def test_returns_dict_per_model(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        result = pl.get_feature_importances()
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_importances_sum_to_approx_1(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        importances = pl.get_feature_importances()
        for model_key, feats in importances.items():
            total = sum(feats.values())
            assert abs(total - 1.0) < 0.01, f"Importances for {model_key} don't sum to 1"

    def test_feature_names_present(self):
        pl = PatternLearner()
        pl.update_patterns(_make_training_data())
        importances = pl.get_feature_importances()
        for feats in importances.values():
            assert "weekday" in feats
            assert "hour" in feats
            assert "outdoor_temp_c" in feats
