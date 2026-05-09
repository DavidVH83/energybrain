"""Tests for energybrain.models."""
from datetime import datetime, time

import pytest

from energybrain.models import (
    Action,
    ActionResult,
    ActionType,
    ApplianceState,
    ApplianceType,
    BatteryDispatchPlan,
    BatteryMode,
    BatteryState,
    DayPlan,
    DeviceStatus,
    EnergyPrice,
    GridState,
    HVACMode,
    HeatPumpState,
    HourlyForecast,
    NotificationType,
    PVState,
    PredictionRecord,
    ScheduledTask,
    SurplusWindow,
    SystemState,
    ThermalModelParams,
    WeatherForecast,
    WeekStrategy,
)


class TestGridState:
    def test_surplus_w_when_injecting(self):
        state = GridState(power_w=-1500.0, daily_import_kwh=0.0, daily_export_kwh=5.0)
        assert state.surplus_w == 1500.0

    def test_surplus_w_when_consuming(self):
        state = GridState(power_w=800.0, daily_import_kwh=2.0, daily_export_kwh=0.0)
        assert state.surplus_w == 0.0

    def test_surplus_w_at_zero(self):
        state = GridState(power_w=0.0, daily_import_kwh=0.0, daily_export_kwh=0.0)
        assert state.surplus_w == 0.0

    def test_default_status_is_unknown(self):
        state = GridState(power_w=0.0, daily_import_kwh=0.0, daily_export_kwh=0.0)
        assert state.status == DeviceStatus.UNKNOWN


class TestSurplusWindow:
    def test_duration_hours(self):
        window = SurplusWindow(start_hour=10, end_hour=15, avg_surplus_w=2000.0, total_energy_kwh=10.0)
        assert window.duration_hours == 5

    def test_duration_hours_same_start_end(self):
        window = SurplusWindow(start_hour=12, end_hour=12, avg_surplus_w=0.0, total_energy_kwh=0.0)
        assert window.duration_hours == 0


class TestBatteryState:
    def test_write_enabled_default_false(self):
        state = BatteryState(soc_pct=80.0, power_w=0.0, temperature_c=25.0)
        assert state.write_enabled is False

    def test_positive_power_is_charging(self):
        state = BatteryState(soc_pct=50.0, power_w=1000.0, temperature_c=25.0)
        assert state.power_w > 0

    def test_negative_power_is_discharging(self):
        state = BatteryState(soc_pct=50.0, power_w=-1000.0, temperature_c=25.0)
        assert state.power_w < 0


class TestBatteryDispatchPlan:
    def test_is_stub_default_true(self):
        plan = BatteryDispatchPlan(
            date=datetime.now(),
            hourly_target_w=[0.0] * 24,
            expected_savings_eur=0.0,
            peak_prevention_kw=0.0,
        )
        assert plan.is_stub is True


class TestThermalModelParams:
    def test_defaults(self):
        params = ThermalModelParams()
        assert params.is_trained is False
        assert params.model_type == "linear"
        assert params.r2_score == 0.0

    def test_cooling_rate_default(self):
        params = ThermalModelParams()
        assert params.cooling_rate_c_per_hour == pytest.approx(0.3)


class TestEnums:
    def test_battery_mode_values(self):
        assert BatteryMode.AUTO.value == "Auto"
        assert BatteryMode.MANUAL.value == "Manual"

    def test_hvac_mode_values(self):
        assert HVACMode.HEAT.value == "heat"
        assert HVACMode.COOL.value == "cool"

    def test_appliance_type_values(self):
        assert ApplianceType.DISHWASHER.value == "dishwasher"
        assert ApplianceType.WASHING_MACHINE.value == "washing_machine"
        assert ApplianceType.DRYER.value == "dryer"

    def test_notification_type_safety_alarm(self):
        assert NotificationType.SAFETY_ALARM.value == "safety_alarm"


class TestAction:
    def test_default_is_stub_false(self):
        action = Action(action_type=ActionType.NO_ACTION, target_entity="")
        assert action.is_stub is False

    def test_default_parameters_empty_dict(self):
        action = Action(action_type=ActionType.NO_ACTION, target_entity="")
        assert action.parameters == {}

    def test_rollback_after_minutes_default_none(self):
        action = Action(action_type=ActionType.NO_ACTION, target_entity="")
        assert action.rollback_after_minutes is None


class TestActionResult:
    def test_success_result(self):
        action = Action(action_type=ActionType.NO_ACTION, target_entity="test")
        result = ActionResult(success=True, action=action)
        assert result.success is True
        assert result.error is None

    def test_failure_result(self):
        action = Action(action_type=ActionType.SET_DHW_BOOST, target_entity="climate.anna")
        result = ActionResult(success=False, action=action, error="Connection timeout")
        assert result.success is False
        assert "timeout" in result.error


class TestPredictionRecord:
    def test_defaults(self):
        rec = PredictionRecord(
            model_name="dhw_demand",
            features={"outdoor_temp_c": 5.0},
            predicted_value=0.75,
        )
        assert rec.actual_value is None
        assert rec.is_correct is None
        assert rec.outcome_at is None


class TestSystemState:
    def test_system_state_creation(self, system_state):
        assert system_state.pv is not None
        assert system_state.battery is not None
        assert system_state.grid is not None
        assert system_state.heat_pump is not None
        assert len(system_state.appliances) == 3

    def test_optional_fields_default_none(self, system_state):
        assert system_state.day_plan is None
        assert system_state.week_strategy is None

    def test_grid_surplus_via_system_state(self, system_state):
        # grid_state has power_w=-1200 → surplus = 1200
        assert system_state.grid.surplus_w == pytest.approx(1200.0)


class TestScheduledTask:
    def test_is_forced_default_false(self):
        task = ScheduledTask(
            name="dishwasher",
            appliance_type=ApplianceType.DISHWASHER,
            planned_start=datetime.now(),
            min_surplus_w=1800.0,
            estimated_duration_hours=2.0,
            hard_deadline=time(20, 0),
            max_wait_hours=4.0,
            priority=1,
        )
        assert task.is_forced is False


class TestWeatherForecast:
    def test_hourly_count(self, weather_forecast):
        assert len(weather_forecast.hourly) == 24

    def test_calibration_factor_default(self, weather_forecast):
        assert weather_forecast.pv_calibration_factor == 1.0
