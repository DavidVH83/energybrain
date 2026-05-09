"""Tests for energybrain.agents.heat_pump_agent."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from energybrain.agents.heat_pump_agent import HeatPumpAgent, _parse_hvac_mode
from energybrain.exceptions import HAStateUnavailableError
from energybrain.models import DeviceStatus, HVACMode
from energybrain.utils.ha_client import HAClient


def _make_ha(states: dict[str, str] | None = None) -> HAClient:
    ha = MagicMock(spec=HAClient)
    states = states or {}

    async def _get_state(entity_id: str) -> dict:
        if entity_id not in states:
            raise HAStateUnavailableError(entity_id)
        return {"state": states[entity_id], "attributes": {}}

    ha.get_state = AsyncMock(side_effect=_get_state)
    ha.call_service = AsyncMock(return_value=[])
    return ha


def _healthy_ha(ssw_modus: str = "comfort") -> HAClient:
    return _make_ha({
        "sensor.anna_temperatuur": "21.0",
        "sensor.smile_anna_buitentemperatuur": "8.5",
        "sensor.anna_instelpunt": "20.5",
        "climate.anna": "heat",
        "sensor.opentherm_sww_temperatuur": "46.1",
        "select.opentherm_ssw_modus": ssw_modus,
    })


class TestHeatPumpAgentCollect:
    async def test_returns_heat_pump_state(self, minimal_env):
        agent = HeatPumpAgent(_healthy_ha())
        state = await agent.collect()
        assert state.indoor_temp_c == pytest.approx(21.0)
        assert state.outdoor_temp_c == pytest.approx(8.5)
        assert state.setpoint_c == pytest.approx(20.5)

    async def test_hvac_mode_heat_parsed(self, minimal_env):
        agent = HeatPumpAgent(_healthy_ha())
        state = await agent.collect()
        assert state.hvac_mode == HVACMode.HEAT

    async def test_status_online_when_all_available(self, minimal_env):
        agent = HeatPumpAgent(_healthy_ha())
        state = await agent.collect()
        assert state.status == DeviceStatus.ONLINE

    async def test_dhw_boost_active_when_modus_boost(self, minimal_env):
        agent = HeatPumpAgent(_healthy_ha(ssw_modus="boost"))
        state = await agent.collect()
        assert state.dhw_boost_active is True

    async def test_dhw_boost_inactive_when_modus_comfort(self, minimal_env):
        agent = HeatPumpAgent(_healthy_ha(ssw_modus="comfort"))
        state = await agent.collect()
        assert state.dhw_boost_active is False

    async def test_dhw_temp_present_when_sensor_online(self, minimal_env):
        agent = HeatPumpAgent(_healthy_ha())
        state = await agent.collect()
        assert state.dhw_temp_c == pytest.approx(46.1)

    async def test_dhw_temp_none_when_sensor_offline(self, minimal_env):
        ha = _make_ha({
            "sensor.anna_temperatuur": "21.0",
            "sensor.smile_anna_buitentemperatuur": "8.5",
            "sensor.anna_instelpunt": "20.5",
            "climate.anna": "heat",
            "select.opentherm_ssw_modus": "comfort",
            # DHW sensor missing → unavailable
        })
        agent = HeatPumpAgent(ha)
        state = await agent.collect()
        assert state.dhw_temp_c is None

    async def test_status_offline_when_indoor_sensor_missing(self, minimal_env):
        ha = _make_ha({
            "sensor.smile_anna_buitentemperatuur": "8.5",
            "sensor.anna_instelpunt": "20.5",
            "climate.anna": "heat",
            "sensor.opentherm_sww_temperatuur": "46.1",
            "select.opentherm_ssw_modus": "comfort",
        })
        agent = HeatPumpAgent(ha)
        state = await agent.collect()
        assert state.status == DeviceStatus.OFFLINE
        assert state.indoor_temp_c == pytest.approx(20.0)  # fallback


class TestDetectManualOverride:
    def test_override_detected_above_threshold(self, minimal_env):
        agent = HeatPumpAgent(MagicMock(spec=HAClient))
        assert agent.detect_manual_override(21.5, 20.0) is True

    def test_no_override_below_threshold(self, minimal_env):
        agent = HeatPumpAgent(MagicMock(spec=HAClient))
        assert agent.detect_manual_override(20.3, 20.0) is False

    def test_no_override_when_brain_changed_this_cycle(self, minimal_env):
        agent = HeatPumpAgent(MagicMock(spec=HAClient))
        assert agent.detect_manual_override(21.5, 20.0, brain_changed_this_cycle=True) is False

    def test_no_override_exactly_at_threshold(self, minimal_env):
        agent = HeatPumpAgent(MagicMock(spec=HAClient))
        # Delta = 0.5 is NOT > 0.5, so no override
        assert agent.detect_manual_override(20.5, 20.0) is False

    def test_override_just_above_threshold(self, minimal_env):
        agent = HeatPumpAgent(MagicMock(spec=HAClient))
        assert agent.detect_manual_override(20.51, 20.0) is True


class TestParseHvacMode:
    def test_heat(self):
        assert _parse_hvac_mode("heat") == HVACMode.HEAT

    def test_cool(self):
        assert _parse_hvac_mode("cool") == HVACMode.COOL

    def test_off(self):
        assert _parse_hvac_mode("off") == HVACMode.OFF

    def test_auto(self):
        assert _parse_hvac_mode("auto") == HVACMode.AUTO

    def test_unknown_defaults_to_auto(self):
        assert _parse_hvac_mode("unknown") == HVACMode.AUTO

    def test_case_insensitive(self):
        assert _parse_hvac_mode("HEAT") == HVACMode.HEAT
