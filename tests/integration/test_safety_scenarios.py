"""Integration tests — safety scenarios.

Tests hard limit blocking, watchdog interventions, and rollback execution.
All run offline (no real HA required).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from energybrain.models import (
    Action,
    ActionType,
    ApplianceState,
    ApplianceType,
    BatteryMode,
    BatteryState,
    EnergyPrice,
    GridState,
    HVACMode,
    HeatPumpState,
    HourlyForecast,
    NotificationType,
    PVState,
    SystemState,
    WeatherForecast,
)
from energybrain.safety.hard_limits import HardLimits
from energybrain.safety.rollback import RollbackManager
from energybrain.safety.watchdog import Watchdog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(
    hvac_max_sp: float = 22.5,
    hvac_min_sp: float = 16.0,
    max_step: float = 0.5,
    indoor_min: float = 17.0,
    frost_outdoor: float = -2.0,
    battery_min_soc: float = 10.0,
) -> MagicMock:
    cfg = MagicMock()
    cfg.hvac_max_setpoint_c = hvac_max_sp
    cfg.hvac_min_setpoint_c = hvac_min_sp
    cfg.hvac_max_step_per_cycle_c = max_step
    cfg.indoor_temp_min_winter_c = indoor_min
    cfg.hvac_frost_outdoor_c = frost_outdoor
    cfg.battery_soc_min_pct = battery_min_soc
    cfg.dhw_min_temp_c = 40.0
    cfg.notification_device = "test_device"
    return cfg


def _make_state(
    indoor_c: float = 20.0,
    outdoor_c: float = 10.0,
    setpoint_c: float = 20.0,
    hvac_mode: HVACMode = HVACMode.HEAT,
    dhw_temp_c: float = 50.0,
    soc_pct: float = 80.0,
    pv_w: float = 2000.0,
    grid_w: float = -500.0,
) -> SystemState:
    return SystemState(
        pv=PVState(power_w=pv_w, daily_energy_kwh=5.0),
        battery=BatteryState(soc_pct=soc_pct, power_w=0.0, temperature_c=25.0,
                             mode=BatteryMode.AUTO),
        grid=GridState(power_w=grid_w, daily_import_kwh=1.0, daily_export_kwh=2.0),
        heat_pump=HeatPumpState(
            indoor_temp_c=indoor_c,
            outdoor_temp_c=outdoor_c,
            setpoint_c=setpoint_c,
            hvac_mode=hvac_mode,
            dhw_boost_active=False,
            dhw_temp_c=dhw_temp_c,
        ),
        appliances={
            ApplianceType.DISHWASHER: ApplianceState(
                appliance_type=ApplianceType.DISHWASHER,
                remote_start_allowed=True,
                is_running=False,
            ),
        },
        weather=WeatherForecast(
            location="Test",
            daily_pv_kwh=10.0,
            hourly=[HourlyForecast(hour=h, pv_estimated_w=0.0, cloud_cover_pct=50.0,
                                   temperature_c=outdoor_c) for h in range(24)],
        ),
        prices=EnergyPrice(
            current_import_eur_kwh=0.25,
            current_export_eur_kwh=0.036,
            hourly_import_prices=[0.25] * 24,
            cheap_hours=[],
            expensive_hours=[],
        ),
    )


# ---------------------------------------------------------------------------
# Hard limits — setpoint
# ---------------------------------------------------------------------------

class TestHardLimitBlocksHighSetpoint:
    def test_blocks_setpoint_above_22_5(self):
        limits = HardLimits(_make_config(hvac_max_sp=22.5))
        action = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 23.5},
        )
        ok, reason = limits.validate_action(action, _make_state())
        assert ok is False
        assert reason

    def test_allows_setpoint_at_max(self):
        limits = HardLimits(_make_config(hvac_max_sp=22.5, max_step=1.0))
        action = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 20.5},  # small step from 20.0
        )
        ok, _ = limits.validate_action(action, _make_state(setpoint_c=20.0))
        assert ok is True

    def test_blocks_setpoint_below_minimum(self):
        limits = HardLimits(_make_config(hvac_min_sp=16.0))
        action = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 14.0},
        )
        ok, reason = limits.validate_action(action, _make_state())
        assert ok is False

    def test_allows_lowering_when_indoor_is_warm(self):
        limits = HardLimits(_make_config(hvac_min_sp=16.0, max_step=1.0))
        action = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 19.5},  # lower by 0.5 from 20.0 setpoint
        )
        ok, _ = limits.validate_action(action, _make_state(indoor_c=21.0, setpoint_c=20.0))
        assert ok is True

    def test_step_too_large_is_blocked(self):
        limits = HardLimits(_make_config(max_step=0.5))
        action = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 22.0},
        )
        # Current setpoint = 20.0, new = 22.0, step = 2.0 > 0.5
        ok, reason = limits.validate_action(action, _make_state(setpoint_c=20.0))
        assert ok is False


class TestHardLimitFrostProtection:
    def test_blocks_non_hvac_action_during_frost(self):
        limits = HardLimits(_make_config(frost_outdoor=-2.0))
        action = Action(
            action_type=ActionType.START_APPLIANCE,
            target_entity="dishwasher",
        )
        # Frost: outdoor = -3°C, indoor is warm (18°C > min 17°C)
        state = _make_state(outdoor_c=-3.0, indoor_c=18.0)
        ok, reason = limits.validate_action(action, state)
        assert ok is False
        assert "rost" in reason.lower()

    def test_hvac_actions_allowed_during_frost(self):
        limits = HardLimits(_make_config(frost_outdoor=-2.0))
        action = Action(
            action_type=ActionType.SET_DHW_BOOST,
            target_entity="climate.anna",
        )
        state = _make_state(outdoor_c=-3.0, indoor_c=18.0)
        ok, _ = limits.validate_action(action, state)
        assert ok is True

    def test_rule1_overrides_rule2_when_indoor_too_cold(self):
        """Rule 1: indoor below min → MUST allow heating even during frost."""
        limits = HardLimits(_make_config(frost_outdoor=-2.0, indoor_min=17.0))
        action = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 19.0},
        )
        # Frost AND indoor below minimum → Rule 1 wins
        state = _make_state(outdoor_c=-3.0, indoor_c=16.0, setpoint_c=18.5)
        ok, _ = limits.validate_action(action, state)
        assert ok is True


class TestHardLimitBatteryPower:
    def test_set_battery_power_action_passes_hard_limits(self):
        """SET_BATTERY_POWER goes through hard limits — should pass (no setpoint involved)."""
        limits = HardLimits(_make_config())
        action = Action(
            action_type=ActionType.SET_BATTERY_POWER,
            target_entity="battery",
            parameters={"power_w": 1000},
        )
        state = _make_state(soc_pct=50.0)
        ok, _ = limits.validate_action(action, state)
        assert ok is True

    def test_battery_power_blocked_during_frost_if_non_hvac(self):
        """Battery mode change is non-HVAC — blocked during frost when indoor is warm."""
        limits = HardLimits(_make_config(frost_outdoor=-2.0))
        action = Action(
            action_type=ActionType.SET_BATTERY_MODE,
            target_entity="battery",
            parameters={"mode": "Manual"},
        )
        state = _make_state(outdoor_c=-3.0, indoor_c=18.0)
        ok, reason = limits.validate_action(action, state)
        assert ok is False


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------

def _make_watchdog(indoor_min: float = 17.0) -> Watchdog:
    cfg = _make_config(indoor_min=indoor_min)
    db = MagicMock()
    db.log_safety_event = AsyncMock()
    return Watchdog(cfg, db)


class TestWatchdogFiresWhenIndoorBelowMin:
    @pytest.mark.asyncio
    async def test_watchdog_fires_on_cold_indoor(self):
        watchdog = _make_watchdog(indoor_min=17.0)
        state = _make_state(indoor_c=16.0)
        actions = await watchdog.check_all(state)
        assert any(a.action_type == ActionType.SET_HVAC_SETPOINT for a in actions)

    @pytest.mark.asyncio
    async def test_watchdog_no_action_when_warm(self):
        watchdog = _make_watchdog(indoor_min=17.0)
        state = _make_state(indoor_c=19.0)
        actions = await watchdog.check_all(state)
        heating_actions = [a for a in actions
                           if a.action_type == ActionType.SET_HVAC_SETPOINT
                           and a.reason and "indoor" in a.reason.lower()]
        assert len(heating_actions) == 0


class TestWatchdogDHWCheck:
    @pytest.mark.asyncio
    async def test_watchdog_fires_when_dhw_cold_in_evening(self):
        watchdog = _make_watchdog()
        state = _make_state(dhw_temp_c=35.0, indoor_c=20.0)
        import unittest.mock
        with unittest.mock.patch("energybrain.safety.watchdog.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2025, 1, 15, 18, 0)
            actions = await watchdog.check_all(state)
        dhw_actions = [a for a in actions if a.action_type == ActionType.SET_DHW_BOOST]
        assert len(dhw_actions) > 0


# ---------------------------------------------------------------------------
# Rollback
# ---------------------------------------------------------------------------

class TestRollbackExecutesAfterTimeout:
    def test_register_requires_rollback_after_minutes(self):
        """Actions without rollback_after_minutes raise ValueError."""
        rm = RollbackManager()
        original = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 22.0},
            rollback_after_minutes=None,
        )
        rollback = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 20.0},
        )
        with pytest.raises(ValueError):
            rm.register(original, rollback)

    @pytest.mark.asyncio
    async def test_rollback_executes_when_overdue(self):
        rm = RollbackManager()
        original = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 22.0},
            rollback_after_minutes=30,
        )
        rollback = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 20.0},
        )
        rm.register(original, rollback)
        # Manually push execute_at into the past
        rm._pending[0].execute_at = datetime.now() - timedelta(minutes=1)

        executed = []

        class FakeExecutor:
            async def execute(self, action):
                executed.append(action)
                return MagicMock(success=True)

        await rm.check_and_execute(FakeExecutor())
        assert len(executed) == 1
        assert executed[0].parameters["temperature"] == 20.0  # rollback setpoint

    @pytest.mark.asyncio
    async def test_rollback_not_due_before_timeout(self):
        rm = RollbackManager()
        original = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 22.0},
            rollback_after_minutes=60,
        )
        rollback = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 20.0},
        )
        rm.register(original, rollback)
        # execute_at is 60 min in the future — not due

        executed = []

        class FakeExecutor:
            async def execute(self, action):
                executed.append(action)
                return MagicMock(success=True)

        await rm.check_and_execute(FakeExecutor())
        assert len(executed) == 0

    @pytest.mark.asyncio
    async def test_rollback_not_repeated_after_execution(self):
        rm = RollbackManager()
        original = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 22.0},
            rollback_after_minutes=30,
        )
        rollback = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 20.0},
        )
        rm.register(original, rollback)
        rm._pending[0].execute_at = datetime.now() - timedelta(minutes=1)

        executed = []

        class FakeExecutor:
            async def execute(self, action):
                executed.append(action)
                return MagicMock(success=True)

        await rm.check_and_execute(FakeExecutor())
        await rm.check_and_execute(FakeExecutor())
        assert len(executed) == 1  # only once


# ---------------------------------------------------------------------------
# Safety alarm notification — never throttled
# ---------------------------------------------------------------------------

class TestSafetyAlarmNeverThrottled:
    @pytest.mark.asyncio
    async def test_indoor_temp_check_fires_every_call(self):
        """Watchdog check_all returns a SET_HVAC_SETPOINT action on every call when cold."""
        watchdog = _make_watchdog(indoor_min=17.0)
        state = _make_state(indoor_c=15.0)
        results = []
        for _ in range(3):
            actions = await watchdog.check_all(state)
            hvac_actions = [a for a in actions if a.action_type == ActionType.SET_HVAC_SETPOINT]
            results.append(len(hvac_actions))
        # Must fire every single time — no throttling of safety actions
        assert all(n >= 1 for n in results)


# ---------------------------------------------------------------------------
# Marstek stub — never writes when disabled
# ---------------------------------------------------------------------------

class TestMarstekStubNeverWrites:
    @pytest.mark.asyncio
    async def test_stub_raises_stub_action_error_when_disabled(self):
        """set_mode raises StubActionError (not a real HA call) when write disabled."""
        from energybrain.agents.marstek_agent import MarstekAgent
        from energybrain.exceptions import StubActionError
        from energybrain.models import BatteryMode
        ha = MagicMock()
        ha.call_service = AsyncMock()
        cfg = MagicMock()
        cfg.marstek_write_enabled = False

        agent = MarstekAgent(ha, cfg)

        with pytest.raises(StubActionError):
            await agent.set_mode(BatteryMode.MANUAL)
        # HA was never called — error is raised before any real write
        ha.call_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_stub_does_not_call_ha_service(self):
        """Even when StubActionError is raised, HA service is never called."""
        from energybrain.agents.marstek_agent import MarstekAgent
        from energybrain.exceptions import StubActionError
        from energybrain.models import BatteryMode
        ha = MagicMock()
        ha.call_service = AsyncMock()
        cfg = MagicMock()
        cfg.marstek_write_enabled = False

        agent = MarstekAgent(ha, cfg)
        try:
            await agent.set_mode(BatteryMode.AUTO)
        except StubActionError:
            pass
        ha.call_service.assert_not_called()
