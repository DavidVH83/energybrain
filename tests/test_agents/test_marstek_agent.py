"""Tests for energybrain.agents.marstek_agent."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from energybrain.agents.marstek_agent import MarstekAgent, _parse_battery_mode
from energybrain.config import load_config
from energybrain.exceptions import HAStateUnavailableError, StubActionError
from energybrain.models import BatteryMode, DeviceStatus
from energybrain.utils.ha_client import HAClient


def _make_ha(states: dict[str, str] | None = None, bools: dict[str, str] | None = None) -> HAClient:
    ha = MagicMock(spec=HAClient)
    states = states or {}
    bools = bools or {}
    all_states = {**states, **bools}

    async def _get_state(entity_id: str) -> dict:
        if entity_id not in all_states:
            raise HAStateUnavailableError(entity_id)
        return {"state": all_states[entity_id], "attributes": {}}

    ha.get_state = AsyncMock(side_effect=_get_state)
    ha.call_service = AsyncMock(return_value=[])
    return ha


def _healthy_ha(ct_connected: str = "on") -> HAClient:
    return _make_ha({
        "sensor.marstek_venuse_state_of_charge": "80",
        "sensor.marstek_venuse_power": "-1.8",
        "sensor.marstek_venuse_battery_temperature": "24.0",
        "select.marstek_venuse_operating_mode": "Auto",
        "binary_sensor.marstek_venuse_ct_connected": ct_connected,
    })


class TestMarstekAgentCollect:
    async def test_returns_battery_state(self, minimal_env):
        agent = MarstekAgent(_healthy_ha(), load_config())
        state = await agent.collect()
        assert state.soc_pct == pytest.approx(80.0)
        assert state.power_w == pytest.approx(-1.8)

    async def test_write_enabled_false_by_default(self, minimal_env):
        agent = MarstekAgent(_healthy_ha(), load_config())
        state = await agent.collect()
        assert state.write_enabled is False

    async def test_status_online_when_ct_connected(self, minimal_env):
        agent = MarstekAgent(_healthy_ha(ct_connected="on"), load_config())
        state = await agent.collect()
        assert state.status == DeviceStatus.ONLINE

    async def test_status_error_when_ct_disconnected(self, minimal_env):
        agent = MarstekAgent(_healthy_ha(ct_connected="off"), load_config())
        state = await agent.collect()
        assert state.status == DeviceStatus.ERROR

    async def test_status_offline_when_sensors_unavailable(self, minimal_env):
        ha = _make_ha({"binary_sensor.marstek_venuse_ct_connected": "on"})
        agent = MarstekAgent(ha, load_config())
        state = await agent.collect()
        assert state.status == DeviceStatus.OFFLINE

    async def test_mode_parsed_correctly(self, minimal_env):
        agent = MarstekAgent(_healthy_ha(), load_config())
        state = await agent.collect()
        assert state.mode == BatteryMode.AUTO

    async def test_temperature_default_when_unavailable(self, minimal_env):
        ha = _make_ha({
            "sensor.marstek_venuse_state_of_charge": "80",
            "sensor.marstek_venuse_power": "0",
            "select.marstek_venuse_operating_mode": "Auto",
            "binary_sensor.marstek_venuse_ct_connected": "on",
        })
        agent = MarstekAgent(ha, load_config())
        state = await agent.collect()
        assert state.temperature_c == pytest.approx(25.0)  # fallback


class TestMarstekAgentSetMode:
    async def test_set_mode_raises_stub_when_disabled(self, minimal_env):
        agent = MarstekAgent(_healthy_ha(), load_config())
        with pytest.raises(StubActionError):
            await agent.set_mode(BatteryMode.PASSIVE)

    async def test_set_mode_calls_service_when_enabled(self, minimal_env, monkeypatch):
        monkeypatch.setenv("MARSTEK_WRITE_ENABLED", "true")
        ha = _healthy_ha()
        agent = MarstekAgent(ha, load_config())
        await agent.set_mode(BatteryMode.PASSIVE)
        ha.call_service.assert_awaited_once()
        call_kwargs = ha.call_service.await_args[1]
        assert call_kwargs["option"] == BatteryMode.PASSIVE.value

    async def test_set_mode_passive_value_is_passive(self, minimal_env, monkeypatch):
        monkeypatch.setenv("MARSTEK_WRITE_ENABLED", "true")
        ha = _healthy_ha()
        agent = MarstekAgent(ha, load_config())
        await agent.set_mode(BatteryMode.PASSIVE)
        call_kwargs = ha.call_service.await_args[1]
        assert call_kwargs["option"] == "Passive"


class TestParseBatteryMode:
    def test_auto(self):
        assert _parse_battery_mode("Auto") == BatteryMode.AUTO

    def test_ai(self):
        assert _parse_battery_mode("AI") == BatteryMode.AI

    def test_manual(self):
        assert _parse_battery_mode("Manual") == BatteryMode.MANUAL

    def test_passive(self):
        assert _parse_battery_mode("Passive") == BatteryMode.PASSIVE

    def test_unknown_defaults_to_auto(self):
        assert _parse_battery_mode("unknown_value") == BatteryMode.AUTO

    def test_case_insensitive(self):
        assert _parse_battery_mode("passive") == BatteryMode.PASSIVE
