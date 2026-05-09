"""Tests for energybrain.safety.hard_limits (HardLimits + CapacityTariffGuard)."""
from datetime import datetime, time, timedelta

import pytest

from energybrain.config import load_config
from energybrain.models import Action, ActionType, ApplianceType, BatteryMode, DeviceStatus
from energybrain.safety.hard_limits import CapacityTariffGuard, HardLimits


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_limits(monkeypatch=None) -> HardLimits:
    return HardLimits(load_config())


def _make_guard(monkeypatch=None) -> CapacityTariffGuard:
    return CapacityTariffGuard(load_config())


def _hvac_action(setpoint: float, current: float = 20.0) -> Action:
    return Action(
        action_type=ActionType.SET_HVAC_SETPOINT,
        target_entity="climate.anna",
        parameters={"temperature": setpoint},
    )


def _battery_action() -> Action:
    return Action(
        action_type=ActionType.SET_BATTERY_POWER,
        target_entity="select.marstek_venuse_operating_mode",
        parameters={"power_w": 500},
    )


# ---------------------------------------------------------------------------
# HardLimits.validate_action — setpoint checks
# ---------------------------------------------------------------------------

class TestValidateSetpoint:
    def test_blocks_setpoint_above_max(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        ok, reason = limits.validate_action(_hvac_action(23.0), system_state)
        assert ok is False
        assert "22.5" in reason

    def test_blocks_setpoint_below_min(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        ok, reason = limits.validate_action(_hvac_action(15.5), system_state)
        assert ok is False
        assert "16.0" in reason

    def test_allows_setpoint_at_max(self, minimal_env, system_state):
        # Set current setpoint close to max so step limit is not triggered
        system_state.heat_pump.setpoint_c = 22.0
        limits = HardLimits(load_config())
        ok, _ = limits.validate_action(_hvac_action(22.5), system_state)
        assert ok is True

    def test_blocks_step_above_max(self, minimal_env, system_state):
        # current setpoint is 20.0, requesting 21.0 = 1.0°C step > 0.5 limit
        limits = HardLimits(load_config())
        ok, reason = limits.validate_action(_hvac_action(21.0), system_state)
        assert ok is False
        assert "step" in reason.lower()

    def test_allows_step_within_max(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        ok, _ = limits.validate_action(_hvac_action(20.5), system_state)
        assert ok is True

    def test_allows_force_heat_when_indoor_below_min(self, minimal_env, system_state):
        """Rule 1: indoor < 17°C → force_heat MUST be allowed even above current setpoint."""
        limits = HardLimits(load_config())
        # Set indoor temp below minimum
        system_state.heat_pump.indoor_temp_c = 16.0
        system_state.heat_pump.setpoint_c = 16.5
        # Request a setpoint increase (0.5°C step — ok, going up is heating)
        ok, _ = limits.validate_action(_hvac_action(17.0, 16.5), system_state)
        assert ok is True

    def test_no_action_always_safe(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        action = Action(action_type=ActionType.NO_ACTION, target_entity="")
        ok, reason = limits.validate_action(action, system_state)
        assert ok is True
        assert reason == ""

    def test_notification_always_safe(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        action = Action(action_type=ActionType.SEND_NOTIFICATION, target_entity="notify")
        ok, _ = limits.validate_action(action, system_state)
        assert ok is True


# ---------------------------------------------------------------------------
# HardLimits.validate_action — frost protection
# ---------------------------------------------------------------------------

class TestFrostProtection:
    def test_blocks_battery_write_when_frost_and_indoor_safe(self, minimal_env, system_state):
        """Rule 2: outdoor < -2°C AND indoor >= 17°C → block non-HVAC actions."""
        limits = HardLimits(load_config())
        system_state.heat_pump.outdoor_temp_c = -3.0
        system_state.heat_pump.indoor_temp_c = 19.0
        ok, reason = limits.validate_action(_battery_action(), system_state)
        assert ok is False
        assert "frost" in reason.lower()

    def test_allows_battery_write_when_no_frost(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        system_state.heat_pump.outdoor_temp_c = 5.0
        system_state.heat_pump.indoor_temp_c = 19.0
        # Battery SOC is 75% — above min
        ok, _ = limits.validate_action(_battery_action(), system_state)
        assert ok is True

    def test_indoor_emergency_overrides_frost_rule(self, minimal_env, system_state):
        """Rule 1 wins over Rule 2: indoor < 17°C → allow heat even in frost."""
        limits = HardLimits(load_config())
        system_state.heat_pump.outdoor_temp_c = -5.0
        system_state.heat_pump.indoor_temp_c = 16.0
        system_state.heat_pump.setpoint_c = 16.5
        ok, _ = limits.validate_action(_hvac_action(17.0, 16.5), system_state)
        assert ok is True


# ---------------------------------------------------------------------------
# HardLimits.validate_action — battery SOC
# ---------------------------------------------------------------------------

class TestBatterySocLimit:
    def test_blocks_battery_power_when_soc_at_minimum(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        system_state.battery.soc_pct = 10.0   # exactly at minimum
        ok, reason = limits.validate_action(_battery_action(), system_state)
        assert ok is False
        assert "SOC" in reason

    def test_blocks_battery_power_when_soc_below_minimum(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        system_state.battery.soc_pct = 5.0
        ok, _ = limits.validate_action(_battery_action(), system_state)
        assert ok is False

    def test_allows_battery_power_when_soc_above_minimum(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        system_state.battery.soc_pct = 75.0
        ok, _ = limits.validate_action(_battery_action(), system_state)
        assert ok is True


# ---------------------------------------------------------------------------
# HardLimits.needs_force_heat
# ---------------------------------------------------------------------------

class TestNeedsForceHeat:
    def test_true_when_indoor_below_17(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        system_state.heat_pump.indoor_temp_c = 16.8
        assert limits.needs_force_heat(system_state) is True

    def test_false_when_indoor_at_threshold(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        system_state.heat_pump.indoor_temp_c = 17.0
        assert limits.needs_force_heat(system_state) is False

    def test_false_when_indoor_above_threshold(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        system_state.heat_pump.indoor_temp_c = 20.5
        assert limits.needs_force_heat(system_state) is False


# ---------------------------------------------------------------------------
# HardLimits.is_frost_protection_active
# ---------------------------------------------------------------------------

class TestFrostProtectionActive:
    def test_true_when_outdoor_below_minus_2_and_indoor_safe(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        system_state.heat_pump.outdoor_temp_c = -3.0
        system_state.heat_pump.indoor_temp_c = 19.0
        assert limits.is_frost_protection_active(system_state) is True

    def test_false_when_outdoor_above_threshold(self, minimal_env, system_state):
        limits = HardLimits(load_config())
        system_state.heat_pump.outdoor_temp_c = 5.0
        assert limits.is_frost_protection_active(system_state) is False

    def test_false_when_indoor_below_minimum(self, minimal_env, system_state):
        """Rule 1 overrides: indoor emergency → frost rule inactive."""
        limits = HardLimits(load_config())
        system_state.heat_pump.outdoor_temp_c = -5.0
        system_state.heat_pump.indoor_temp_c = 16.5
        assert limits.is_frost_protection_active(system_state) is False


# ---------------------------------------------------------------------------
# HardLimits.clamp_setpoint
# ---------------------------------------------------------------------------

class TestClampSetpoint:
    def test_clamps_above_max(self, minimal_env):
        limits = HardLimits(load_config())
        assert limits.clamp_setpoint(25.0, 20.0) == pytest.approx(20.5)

    def test_clamps_below_min(self, minimal_env):
        limits = HardLimits(load_config())
        assert limits.clamp_setpoint(14.0, 20.0) == pytest.approx(19.5)

    def test_clamps_step_up(self, minimal_env):
        limits = HardLimits(load_config())
        result = limits.clamp_setpoint(22.0, 20.0)
        assert result == pytest.approx(20.5)

    def test_clamps_step_down(self, minimal_env):
        limits = HardLimits(load_config())
        result = limits.clamp_setpoint(18.0, 20.0)
        assert result == pytest.approx(19.5)

    def test_no_clamp_needed(self, minimal_env):
        limits = HardLimits(load_config())
        result = limits.clamp_setpoint(20.3, 20.0)
        assert result == pytest.approx(20.3)


# ---------------------------------------------------------------------------
# CapacityTariffGuard.is_cooking_peak
# ---------------------------------------------------------------------------

class TestIsCookingPeak:
    def test_true_during_cooking_window(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        assert guard.is_cooking_peak(time(17, 30)) is True

    def test_true_at_window_start(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        assert guard.is_cooking_peak(time(17, 0)) is True

    def test_true_at_window_end(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        assert guard.is_cooking_peak(time(18, 30)) is True

    def test_false_before_window(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        assert guard.is_cooking_peak(time(16, 59)) is False

    def test_false_after_window(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        assert guard.is_cooking_peak(time(18, 31)) is False

    def test_false_morning(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        assert guard.is_cooking_peak(time(11, 0)) is False

    def test_uses_pattern_learner_when_available(self, minimal_env):
        guard = CapacityTariffGuard(load_config())

        class MockLearner:
            def get_cooking_peak(self, weekday, **kwargs):
                return time(16, 0), time(17, 0)

        # 16:30 is inside the mock window → True
        assert guard.is_cooking_peak(time(16, 30), pattern_learner=MockLearner()) is True
        # 17:30 is outside the mock window → False
        assert guard.is_cooking_peak(time(17, 30), pattern_learner=MockLearner()) is False

    def test_falls_back_to_defaults_when_learner_raises(self, minimal_env):
        guard = CapacityTariffGuard(load_config())

        class BrokenLearner:
            def get_cooking_peak(self, weekday, **kwargs):
                raise RuntimeError("not trained")

        # Default window: 17:00-18:30
        assert guard.is_cooking_peak(time(17, 30), pattern_learner=BrokenLearner()) is True


# ---------------------------------------------------------------------------
# CapacityTariffGuard.can_start_appliance
# ---------------------------------------------------------------------------

class TestCanStartAppliance:
    def test_blocks_during_cooking_peak_normal_start(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        ok, reason = guard.can_start_appliance(
            ApplianceType.DISHWASHER,
            last_appliance_start=None,
            current_time=time(17, 30),
        )
        assert ok is False
        assert "cooking peak" in reason.lower()

    def test_allows_outside_cooking_peak(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        ok, _ = guard.can_start_appliance(
            ApplianceType.DISHWASHER,
            last_appliance_start=None,
            current_time=time(11, 0),
        )
        assert ok is True

    def test_blocks_stagger_within_15_min(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        recent = datetime.now() - timedelta(minutes=10)
        ok, reason = guard.can_start_appliance(
            ApplianceType.WASHING_MACHINE,
            last_appliance_start=recent,
            current_time=time(11, 0),
        )
        assert ok is False
        assert "stagger" in reason.lower()

    def test_allows_after_stagger_gap(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        old = datetime.now() - timedelta(minutes=20)
        ok, _ = guard.can_start_appliance(
            ApplianceType.WASHING_MACHINE,
            last_appliance_start=old,
            current_time=time(11, 0),
        )
        assert ok is True

    def test_force_start_during_peak_with_flexible_deadline(self, minimal_env):
        """Force start in peak with deadline >= 18:45 → prefer to wait."""
        guard = CapacityTariffGuard(load_config())
        ok, reason = guard.can_start_appliance(
            ApplianceType.DRYER,
            last_appliance_start=None,
            is_force_start=True,
            hard_deadline=time(20, 0),   # deadline is flexible
            current_time=time(17, 30),
        )
        assert ok is False
        assert "prefer_wait" in reason

    def test_force_start_during_peak_with_tight_deadline(self, minimal_env):
        """Force start in peak with deadline < 18:45 → start despite peak."""
        guard = CapacityTariffGuard(load_config())
        ok, reason = guard.can_start_appliance(
            ApplianceType.DRYER,
            last_appliance_start=None,
            is_force_start=True,
            hard_deadline=time(18, 30),  # tight deadline
            current_time=time(17, 30),
        )
        assert ok is True
        assert "forced_despite_peak" in reason

    def test_force_start_outside_peak(self, minimal_env):
        """Force start outside peak → always allowed."""
        guard = CapacityTariffGuard(load_config())
        ok, reason = guard.can_start_appliance(
            ApplianceType.DISHWASHER,
            last_appliance_start=None,
            is_force_start=True,
            current_time=time(10, 0),
        )
        assert ok is True
        assert reason == "forced"


# ---------------------------------------------------------------------------
# CapacityTariffGuard.next_allowed_start
# ---------------------------------------------------------------------------

class TestNextAllowedStart:
    def test_returns_now_when_no_previous_start(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        before = datetime.now()
        result = guard.next_allowed_start(None)
        assert result >= before

    def test_returns_15_min_after_last_start(self, minimal_env):
        guard = CapacityTariffGuard(load_config())
        last = datetime.now() - timedelta(minutes=5)
        result = guard.next_allowed_start(last)
        expected = last + timedelta(minutes=15)
        assert abs((result - expected).total_seconds()) < 2
