"""Tests for energybrain.agents.ha_control_agent."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from energybrain.agents.ha_control_agent import HAControlAgent
from energybrain.exceptions import HAStateUnavailableError
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


def _healthy_ha() -> HAClient:
    return _make_ha({
        "input_boolean.energybrain_enabled": "on",
        "input_select.energybrain_mode": "auto",
        "input_boolean.energybrain_vacation_mode": "off",
        "input_boolean.energybrain_dhw_boost_now": "off",
        "input_number.energybrain_dhw_target_temp": "55.0",
        "input_datetime.energybrain_vacation_start": "2026-06-01T00:00:00",
        "input_datetime.energybrain_vacation_end": "2026-06-14T00:00:00",
    })


class TestHAControlAgentCollect:
    async def test_returns_control_state(self, minimal_env):
        agent = HAControlAgent(_healthy_ha())
        state = await agent.get_control_state()
        assert state.brain_enabled is True
        assert state.brain_mode == "auto"

    async def test_brain_disabled(self, minimal_env):
        ha = _make_ha({
            "input_boolean.energybrain_enabled": "off",
            "input_select.energybrain_mode": "auto",
            "input_boolean.energybrain_vacation_mode": "off",
            "input_boolean.energybrain_dhw_boost_now": "off",
            "input_number.energybrain_dhw_target_temp": "55.0",
        })
        agent = HAControlAgent(ha)
        state = await agent.get_control_state()
        assert state.brain_enabled is False

    async def test_vacation_active(self, minimal_env):
        ha = _make_ha({
            "input_boolean.energybrain_enabled": "on",
            "input_select.energybrain_mode": "auto",
            "input_boolean.energybrain_vacation_mode": "on",
            "input_boolean.energybrain_dhw_boost_now": "off",
            "input_number.energybrain_dhw_target_temp": "55.0",
        })
        agent = HAControlAgent(ha)
        state = await agent.get_control_state()
        assert state.vacation_active is True

    async def test_vacation_dates_parsed(self, minimal_env):
        agent = HAControlAgent(_healthy_ha())
        state = await agent.get_control_state()
        assert state.vacation_start is not None
        assert state.vacation_start.year == 2026
        assert state.vacation_start.month == 6

    async def test_vacation_dates_none_when_missing(self, minimal_env):
        ha = _make_ha({
            "input_boolean.energybrain_enabled": "on",
            "input_select.energybrain_mode": "auto",
            "input_boolean.energybrain_vacation_mode": "off",
            "input_boolean.energybrain_dhw_boost_now": "off",
            "input_number.energybrain_dhw_target_temp": "55.0",
        })
        agent = HAControlAgent(ha)
        state = await agent.get_control_state()
        assert state.vacation_start is None
        assert state.vacation_end is None

    async def test_dhw_boost_now(self, minimal_env):
        ha = _make_ha({
            "input_boolean.energybrain_enabled": "on",
            "input_select.energybrain_mode": "auto",
            "input_boolean.energybrain_vacation_mode": "off",
            "input_boolean.energybrain_dhw_boost_now": "on",
            "input_number.energybrain_dhw_target_temp": "60.0",
        })
        agent = HAControlAgent(ha)
        state = await agent.get_control_state()
        assert state.dhw_boost_now is True
        assert state.dhw_target_temp == pytest.approx(60.0)

    async def test_defaults_when_all_entities_unavailable(self, minimal_env):
        agent = HAControlAgent(_make_ha({}))
        state = await agent.get_control_state()
        assert state.brain_enabled is True
        assert state.vacation_active is False

    async def test_collect_equals_get_control_state(self, minimal_env):
        agent = HAControlAgent(_healthy_ha())
        via_collect = await agent.collect()
        # Re-create agent for fresh call (avoid mock state issues)
        agent2 = HAControlAgent(_healthy_ha())
        via_get = await agent2.get_control_state()
        assert via_collect.brain_enabled == via_get.brain_enabled
        assert via_collect.brain_mode == via_get.brain_mode


class TestHAControlAgentUpdateStatus:
    async def test_update_status_calls_four_services(self, minimal_env):
        ha = _healthy_ha()
        agent = HAControlAgent(ha)
        await agent.update_status(
            status="running",
            last_action="setpoint 20.5°C",
            today_plan="dishwasher at 13:00",
            next_action="check battery at 14:00",
        )
        assert ha.call_service.await_count == 4

    async def test_update_status_writes_correct_entity(self, minimal_env):
        ha = _healthy_ha()
        agent = HAControlAgent(ha)
        await agent.update_status("running", "test", "plan", "next")
        calls = ha.call_service.await_args_list
        entities = [c[1]["entity_id"] for c in calls]
        assert "input_text.energybrain_status" in entities
        assert "input_text.energybrain_last_action" in entities
