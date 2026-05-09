"""Tests for energybrain.agents.home_connect_agent."""
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from energybrain.agents.home_connect_agent import HomeConnectAgent
from energybrain.exceptions import HAStateUnavailableError
from energybrain.models import ApplianceType, DeviceStatus
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


def _all_idle_ha() -> HAClient:
    return _make_ha({
        "binary_sensor.vaatwasser_start_op_afstand": "on",
        "sensor.vaatwasser_status": "ready",
        "binary_sensor.wasmachine_start_op_afstand": "off",
        "sensor.wasmachine_status": "inactive",
        "binary_sensor.droger_start_op_afstand": "off",
        "sensor.droger_status": "inactive",
    })


class TestHomeConnectAgentCollect:
    async def test_returns_dict_with_all_three_appliances(self, minimal_env):
        agent = HomeConnectAgent(_all_idle_ha())
        result = await agent.collect()
        assert set(result.keys()) == {ApplianceType.DISHWASHER, ApplianceType.WASHING_MACHINE, ApplianceType.DRYER}

    async def test_dishwasher_remote_allowed_when_sensor_on(self, minimal_env):
        agent = HomeConnectAgent(_all_idle_ha())
        result = await agent.collect()
        assert result[ApplianceType.DISHWASHER].remote_start_allowed is True

    async def test_washer_remote_not_allowed_when_sensor_off(self, minimal_env):
        agent = HomeConnectAgent(_all_idle_ha())
        result = await agent.collect()
        assert result[ApplianceType.WASHING_MACHINE].remote_start_allowed is False

    async def test_not_running_when_status_inactive(self, minimal_env):
        agent = HomeConnectAgent(_all_idle_ha())
        result = await agent.collect()
        assert result[ApplianceType.WASHING_MACHINE].is_running is False

    async def test_running_when_status_run(self, minimal_env):
        ha = _make_ha({
            "binary_sensor.vaatwasser_start_op_afstand": "off",
            "sensor.vaatwasser_status": "run",
            "binary_sensor.wasmachine_start_op_afstand": "off",
            "sensor.wasmachine_status": "inactive",
            "binary_sensor.droger_start_op_afstand": "off",
            "sensor.droger_status": "inactive",
        })
        agent = HomeConnectAgent(ha)
        result = await agent.collect()
        assert result[ApplianceType.DISHWASHER].is_running is True

    async def test_running_when_status_delayedstart(self, minimal_env):
        ha = _make_ha({
            "binary_sensor.vaatwasser_start_op_afstand": "on",
            "sensor.vaatwasser_status": "delayedstart",
            "binary_sensor.wasmachine_start_op_afstand": "off",
            "sensor.wasmachine_status": "inactive",
            "binary_sensor.droger_start_op_afstand": "off",
            "sensor.droger_status": "inactive",
        })
        agent = HomeConnectAgent(ha)
        result = await agent.collect()
        assert result[ApplianceType.DISHWASHER].is_running is True

    async def test_offline_when_remote_sensor_unavailable(self, minimal_env):
        ha = _make_ha({
            "sensor.vaatwasser_status": "inactive",
            "binary_sensor.wasmachine_start_op_afstand": "off",
            "sensor.wasmachine_status": "inactive",
            "binary_sensor.droger_start_op_afstand": "off",
            "sensor.droger_status": "inactive",
        })
        agent = HomeConnectAgent(ha)
        result = await agent.collect()
        assert result[ApplianceType.DISHWASHER].status == DeviceStatus.OFFLINE


class TestHomeConnectAgentStartAppliance:
    async def test_start_calls_power_switch(self, minimal_env):
        ha = _all_idle_ha()
        agent = HomeConnectAgent(ha)
        await agent.start_appliance(ApplianceType.DISHWASHER)
        ha.call_service.assert_awaited()
        # Last call should be the power switch turn_on
        last_call = ha.call_service.await_args
        assert last_call[0][0] == "switch"
        assert last_call[0][1] == "turn_on"
        assert last_call[1]["entity_id"] == "switch.vaatwasser_inschakelen"

    async def test_start_with_delay_sets_delay_first(self, minimal_env):
        ha = _all_idle_ha()
        agent = HomeConnectAgent(ha)
        await agent.start_appliance(ApplianceType.DISHWASHER, delay_seconds=3600)
        # Two calls: set_value then turn_on
        assert ha.call_service.await_count == 2
        first_call = ha.call_service.await_args_list[0]
        assert first_call[0][0] == "number"
        assert first_call[0][1] == "set_value"

    async def test_start_no_delay_calls_only_power_switch(self, minimal_env):
        ha = _all_idle_ha()
        agent = HomeConnectAgent(ha)
        await agent.start_appliance(ApplianceType.DISHWASHER, delay_seconds=0)
        assert ha.call_service.await_count == 1
