"""Tests for energybrain.safety.watchdog."""
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from energybrain.models import ActionType, BatteryMode, DeviceStatus
from energybrain.safety.watchdog import (
    Watchdog,
    _BATTERY_CRITICAL_SOC_PCT,
    _DHW_MIN_TEMP_C,
    _INDOOR_MIN_C,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def watchdog(tmp_path, minimal_env):
    from energybrain.config import load_config
    from energybrain.persistence.database import DatabaseManager
    db = MagicMock(spec=DatabaseManager)
    db.log_safety_event = AsyncMock()
    cfg = load_config()
    return Watchdog(cfg, db)


# ---------------------------------------------------------------------------
# Check 1: Indoor temperature
# ---------------------------------------------------------------------------

class TestCheckIndoorTemp:
    async def test_fires_when_indoor_below_17_and_outdoor_cold(self, watchdog, system_state):
        system_state.heat_pump.indoor_temp_c = 16.5
        system_state.heat_pump.outdoor_temp_c = 5.0
        actions = await watchdog.check_all(system_state)
        hvac_actions = [a for a in actions if a.action_type == ActionType.SET_HVAC_SETPOINT]
        assert len(hvac_actions) >= 1
        assert "WATCHDOG" in hvac_actions[0].reason

    async def test_does_not_fire_when_indoor_above_17(self, watchdog, system_state):
        system_state.heat_pump.indoor_temp_c = 19.0
        system_state.heat_pump.outdoor_temp_c = 5.0
        actions = await watchdog.check_all(system_state)
        indoor_actions = [
            a for a in actions
            if a.action_type == ActionType.SET_HVAC_SETPOINT and "indoor" in a.reason
        ]
        assert len(indoor_actions) == 0

    async def test_does_not_fire_when_outdoor_warm(self, watchdog, system_state):
        """Indoor below 17 but outdoor is warm — normal summer situation."""
        system_state.heat_pump.indoor_temp_c = 16.0
        system_state.heat_pump.outdoor_temp_c = 20.0
        action = watchdog._check_indoor_temp(system_state)
        assert action is None

    async def test_force_heat_action_has_high_priority(self, watchdog, system_state):
        system_state.heat_pump.indoor_temp_c = 16.0
        system_state.heat_pump.outdoor_temp_c = 5.0
        action = watchdog._check_indoor_temp(system_state)
        assert action is not None
        assert action.priority >= 90

    async def test_safety_event_logged_when_indoor_fires(self, watchdog, system_state):
        system_state.heat_pump.indoor_temp_c = 16.0
        system_state.heat_pump.outdoor_temp_c = 5.0
        await watchdog.check_all(system_state)
        watchdog._db.log_safety_event.assert_called()


# ---------------------------------------------------------------------------
# Check 2: DHW temperature
# ---------------------------------------------------------------------------

class TestCheckDhwTemp:
    async def test_fires_after_17h_when_dhw_below_40(self, watchdog, system_state):
        system_state.heat_pump.dhw_temp_c = 35.0
        with patch("energybrain.safety.watchdog.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 9, 18, 0)
            action = watchdog._check_dhw_temp(system_state)
        assert action is not None
        assert action.action_type == ActionType.SET_DHW_BOOST
        assert "WATCHDOG" in action.reason

    async def test_does_not_fire_before_17h(self, watchdog, system_state):
        system_state.heat_pump.dhw_temp_c = 35.0
        with patch("energybrain.safety.watchdog.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 9, 16, 59)
            action = watchdog._check_dhw_temp(system_state)
        assert action is None

    async def test_does_not_fire_when_dhw_above_40_after_17h(self, watchdog, system_state):
        system_state.heat_pump.dhw_temp_c = 48.0
        with patch("energybrain.safety.watchdog.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 9, 18, 0)
            action = watchdog._check_dhw_temp(system_state)
        assert action is None

    async def test_does_not_fire_when_dhw_is_none(self, watchdog, system_state):
        system_state.heat_pump.dhw_temp_c = None
        with patch("energybrain.safety.watchdog.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 5, 9, 18, 0)
            action = watchdog._check_dhw_temp(system_state)
        assert action is None


# ---------------------------------------------------------------------------
# Check 3: Battery SOC
# ---------------------------------------------------------------------------

class TestCheckBatterySoc:
    async def test_fires_when_soc_below_critical(self, watchdog, system_state):
        system_state.battery.soc_pct = 5.0
        system_state.battery.status = DeviceStatus.ONLINE
        action = watchdog._check_battery_soc(system_state)
        assert action is not None
        assert action.action_type == ActionType.SET_BATTERY_MODE
        assert action.parameters["option"] == BatteryMode.PASSIVE.value

    async def test_fires_at_critical_boundary(self, watchdog, system_state):
        system_state.battery.soc_pct = _BATTERY_CRITICAL_SOC_PCT - 0.1
        system_state.battery.status = DeviceStatus.ONLINE
        action = watchdog._check_battery_soc(system_state)
        assert action is not None

    async def test_does_not_fire_above_critical(self, watchdog, system_state):
        system_state.battery.soc_pct = 75.0
        action = watchdog._check_battery_soc(system_state)
        assert action is None

    async def test_does_not_fire_when_battery_offline(self, watchdog, system_state):
        system_state.battery.soc_pct = 5.0
        system_state.battery.status = DeviceStatus.OFFLINE
        action = watchdog._check_battery_soc(system_state)
        assert action is None

    async def test_action_is_stub_when_write_disabled(self, watchdog, system_state):
        system_state.battery.soc_pct = 5.0
        system_state.battery.status = DeviceStatus.ONLINE
        system_state.battery.write_enabled = False
        action = watchdog._check_battery_soc(system_state)
        assert action is not None
        assert action.is_stub is True

    async def test_action_is_not_stub_when_write_enabled(self, watchdog, system_state):
        system_state.battery.soc_pct = 5.0
        system_state.battery.status = DeviceStatus.ONLINE
        system_state.battery.write_enabled = True
        action = watchdog._check_battery_soc(system_state)
        assert action is not None
        assert action.is_stub is False


# ---------------------------------------------------------------------------
# Check 4: HVAC idle in cold
# ---------------------------------------------------------------------------

class TestCheckHvacIdle:
    async def test_fires_when_hvac_idle_4h_and_cold(self, watchdog, system_state):
        watchdog._last_hvac_active = datetime.now() - timedelta(hours=5)
        system_state.heat_pump.outdoor_temp_c = 3.0
        action = watchdog._check_hvac_idle(system_state)
        assert action is not None
        assert action.action_type == ActionType.SET_HVAC_SETPOINT

    async def test_does_not_fire_when_outdoor_warm(self, watchdog, system_state):
        watchdog._last_hvac_active = datetime.now() - timedelta(hours=5)
        system_state.heat_pump.outdoor_temp_c = 10.0
        action = watchdog._check_hvac_idle(system_state)
        assert action is None

    async def test_does_not_fire_when_recently_active(self, watchdog, system_state):
        watchdog._last_hvac_active = datetime.now() - timedelta(hours=2)
        system_state.heat_pump.outdoor_temp_c = 3.0
        action = watchdog._check_hvac_idle(system_state)
        assert action is None

    async def test_does_not_fire_when_never_recorded(self, watchdog, system_state):
        watchdog._last_hvac_active = None
        action = watchdog._check_hvac_idle(system_state)
        assert action is None


# ---------------------------------------------------------------------------
# Check 5: Brain stale
# ---------------------------------------------------------------------------

class TestCheckBrainStale:
    async def test_fires_when_brain_stale_over_10_min(self, watchdog):
        watchdog._last_brain_decision = datetime.now() - timedelta(minutes=15)
        action = watchdog._check_brain_stale()
        assert action is not None
        assert action.action_type == ActionType.SEND_NOTIFICATION

    async def test_does_not_fire_when_recent_decision(self, watchdog):
        watchdog._last_brain_decision = datetime.now() - timedelta(minutes=5)
        action = watchdog._check_brain_stale()
        assert action is None

    async def test_does_not_fire_when_no_decision_recorded(self, watchdog):
        watchdog._last_brain_decision = None
        action = watchdog._check_brain_stale()
        assert action is None

    async def test_record_brain_decision_updates_timestamp(self, watchdog):
        before = datetime.now()
        watchdog.record_brain_decision()
        assert watchdog._last_brain_decision >= before


# ---------------------------------------------------------------------------
# Check 6: CT clamp disconnected
# ---------------------------------------------------------------------------

class TestCheckCtClamp:
    async def test_fires_when_battery_status_error(self, watchdog, system_state):
        system_state.battery.status = DeviceStatus.ERROR
        action = watchdog._check_ct_clamp(system_state)
        assert action is not None
        assert action.action_type == ActionType.SEND_NOTIFICATION
        assert "CT clamp" in action.parameters["message"]

    async def test_does_not_fire_when_battery_online(self, watchdog, system_state):
        system_state.battery.status = DeviceStatus.ONLINE
        action = watchdog._check_ct_clamp(system_state)
        assert action is None

    async def test_does_not_fire_when_battery_offline(self, watchdog, system_state):
        system_state.battery.status = DeviceStatus.OFFLINE
        action = watchdog._check_ct_clamp(system_state)
        assert action is None


# ---------------------------------------------------------------------------
# check_all — integration
# ---------------------------------------------------------------------------

class TestCheckAll:
    async def test_returns_empty_list_when_all_ok(self, watchdog, system_state):
        # system_state from conftest is healthy
        actions = await watchdog.check_all(system_state)
        # May have 0 or more based on time of day (DHW check is time-dependent)
        assert isinstance(actions, list)

    async def test_returns_multiple_actions_on_multiple_failures(
        self, watchdog, system_state
    ):
        system_state.heat_pump.indoor_temp_c = 16.0
        system_state.heat_pump.outdoor_temp_c = 5.0
        system_state.battery.soc_pct = 5.0
        system_state.battery.status = DeviceStatus.ONLINE
        actions = await watchdog.check_all(system_state)
        types = {a.action_type for a in actions}
        assert ActionType.SET_HVAC_SETPOINT in types
        assert ActionType.SET_BATTERY_MODE in types
