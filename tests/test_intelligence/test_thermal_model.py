"""Tests for energybrain.intelligence.thermal_model."""
from datetime import datetime, timedelta

import pytest

from energybrain.intelligence.thermal_model import MIN_SAMPLES, R2_UPGRADE_THRESHOLD, ThermalModel
from energybrain.models import ThermalModelParams


def _make_obs(n: int = MIN_SAMPLES, hvac_active: bool = False) -> list[dict]:
    """Generate n synthetic training observations."""
    obs = []
    for i in range(n):
        indoor = 20.0 + (i % 3) * 0.1
        outdoor = 8.0 + (i % 5) * 0.5
        obs.append({
            "outdoor_temp_c": outdoor,
            "solar_radiation_w_m2": float((i % 12) * 50),
            "wind_speed_ms": 3.0,
            "hour_of_day": i % 24,
            "hvac_active": hvac_active,
            "indoor_temp_c": indoor,
            "delta_indoor_c_per_hour": 0.1 if hvac_active else -0.05,
        })
    return obs


class TestThermalModelIsReady:
    def test_not_ready_initially(self):
        model = ThermalModel()
        assert model.is_ready() is False

    def test_not_ready_with_few_observations(self):
        model = ThermalModel()
        for obs in _make_obs(100):
            model.add_observation(obs)
        assert model.is_ready() is False

    def test_ready_after_training_with_enough_data(self):
        model = ThermalModel()
        data = _make_obs(MIN_SAMPLES)
        model.update_model(data)
        assert model.is_ready() is True


class TestAddObservation:
    def test_observations_accumulate(self):
        model = ThermalModel()
        obs = {"outdoor_temp_c": 5.0, "delta_indoor_c_per_hour": -0.1}
        model.add_observation(obs)
        assert len(model._observations) == 1


class TestPredictTemperature:
    def test_fallback_returns_list_of_n_values(self):
        model = ThermalModel()
        result = model.predict_temperature(
            current_indoor_c=20.0,
            outdoor_forecast=[8.0] * 4,
            solar_forecast=[0.0] * 4,
            wind_forecast=[3.0] * 4,
            hvac_plan=[False] * 4,
        )
        assert len(result) == 4

    def test_hvac_on_increases_temperature_in_fallback(self):
        model = ThermalModel()
        with_hvac = model.predict_temperature(
            20.0, [8.0], [0.0], [3.0], [True]
        )
        without_hvac = model.predict_temperature(
            20.0, [8.0], [0.0], [3.0], [False]
        )
        assert with_hvac[0] > without_hvac[0]

    def test_trained_model_returns_predictions(self):
        model = ThermalModel()
        data = _make_obs(MIN_SAMPLES)
        model.update_model(data)
        result = model.predict_temperature(20.0, [8.0] * 3, [0.0] * 3, [3.0] * 3, [False] * 3)
        assert len(result) == 3
        assert all(isinstance(v, float) for v in result)

    def test_handles_mismatched_forecast_lengths(self):
        model = ThermalModel()
        result = model.predict_temperature(
            current_indoor_c=20.0,
            outdoor_forecast=[8.0] * 6,
            solar_forecast=[0.0] * 4,
            wind_forecast=[3.0] * 2,
            hvac_plan=[False] * 6,
        )
        # n_hours = min(len(outdoor), len(hvac_plan)) = 6
        assert len(result) == 6


class TestPredictDhwTemperature:
    def test_temperature_decreases_over_time(self):
        model = ThermalModel()
        temp_2h = model.predict_dhw_temperature(60.0, outdoor_temp_c=10.0, hours_ahead=2)
        temp_4h = model.predict_dhw_temperature(60.0, outdoor_temp_c=10.0, hours_ahead=4)
        assert temp_2h > temp_4h

    def test_at_zero_hours_returns_current_temp(self):
        model = ThermalModel()
        result = model.predict_dhw_temperature(60.0, outdoor_temp_c=10.0, hours_ahead=0)
        assert result == pytest.approx(60.0)

    def test_converges_to_outdoor_temp(self):
        model = ThermalModel()
        result = model.predict_dhw_temperature(60.0, outdoor_temp_c=15.0, hours_ahead=1000)
        assert abs(result - 15.0) < 0.01


class TestShouldPreheat:
    def test_no_preheat_when_already_warm(self):
        model = ThermalModel()
        now = datetime.now()
        desired = now + timedelta(hours=2)
        result = model.should_preheat(
            target_temp_c=20.0,
            current_temp_c=22.0,
            desired_time=desired,
            outdoor_forecast=[8.0] * 2,
            solar_forecast=[0.0] * 2,
            wind_forecast=[3.0] * 2,
        )
        # Already warm → start at desired time
        assert result == desired

    def test_preheat_starts_before_desired_time(self):
        model = ThermalModel()
        now = datetime.now()
        desired = now + timedelta(hours=3)
        result = model.should_preheat(
            target_temp_c=22.0,
            current_temp_c=18.0,
            desired_time=desired,
            outdoor_forecast=[5.0] * 3,
            solar_forecast=[0.0] * 3,
            wind_forecast=[3.0] * 3,
        )
        assert result < desired


class TestUpdateModel:
    def test_returns_thermal_model_params(self):
        model = ThermalModel()
        params = model.update_model(_make_obs(MIN_SAMPLES))
        assert isinstance(params, ThermalModelParams)

    def test_model_is_trained_after_update(self):
        model = ThermalModel()
        params = model.update_model(_make_obs(MIN_SAMPLES))
        assert params.is_trained is True

    def test_not_trained_with_insufficient_data(self):
        model = ThermalModel()
        params = model.update_model(_make_obs(100))
        assert params.is_trained is False

    def test_model_type_is_linear_initially(self):
        model = ThermalModel()
        params = model.update_model(_make_obs(MIN_SAMPLES))
        assert params.model_type == "linear"

    def test_r2_score_populated(self):
        model = ThermalModel()
        params = model.update_model(_make_obs(MIN_SAMPLES))
        assert isinstance(params.r2_score, float)

    def test_samples_count_reflects_data(self):
        model = ThermalModel()
        data = _make_obs(MIN_SAMPLES)
        params = model.update_model(data)
        assert params.samples_count == len(data)


class TestEvaluateUpgrade:
    def test_no_upgrade_when_not_ready(self):
        model = ThermalModel()
        assert model.evaluate_upgrade() is False

    def test_no_upgrade_when_r2_good(self):
        model = ThermalModel()
        model.update_model(_make_obs(MIN_SAMPLES))
        # Force a good R2 score
        model._params = ThermalModelParams(r2_score=0.92, is_trained=True)
        model._model = object()  # Non-None so is_ready passes
        assert model.evaluate_upgrade() is False

    def test_upgrade_when_r2_below_threshold(self):
        model = ThermalModel()
        model.update_model(_make_obs(MIN_SAMPLES))
        model._params = ThermalModelParams(r2_score=0.70, is_trained=True)
        result = model.evaluate_upgrade()
        assert result is True
        assert model._is_gbr is True

    def test_no_second_upgrade_after_gbr_active(self):
        model = ThermalModel()
        model.update_model(_make_obs(MIN_SAMPLES))
        model._params = ThermalModelParams(r2_score=0.70, is_trained=True)
        model.evaluate_upgrade()
        # Already GBR — should return False
        assert model.evaluate_upgrade() is False
