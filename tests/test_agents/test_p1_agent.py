"""Tests for energybrain.agents.p1_agent."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from energybrain.agents.p1_agent import P1Agent
from energybrain.exceptions import HAStateUnavailableError
from energybrain.models import DeviceStatus
from energybrain.utils.ha_client import HAClient


def _make_ha(states: dict[str, str] | None = None) -> HAClient:
    """Build a mocked HAClient returning the provided entity states."""
    ha = MagicMock(spec=HAClient)
    states = states or {}

    async def _get_state(entity_id: str) -> dict:
        if entity_id not in states:
            raise HAStateUnavailableError(entity_id)
        return {"state": states[entity_id], "attributes": {}}

    ha.get_state = AsyncMock(side_effect=_get_state)
    return ha


def _healthy_ha() -> HAClient:
    return _make_ha({
        "sensor.p1_meter_vermogen": "244",
        "sensor.p1_meter_energie_import_tarief_1": "1234.5",
        "sensor.p1_meter_energie_import_tarief_2": "567.8",
        "sensor.p1_meter_energie_export_tarief_1": "8000.0",
        "sensor.p1_meter_energie_export_tarief_2": "336.3",
    })


class TestP1AgentCollect:
    async def test_returns_grid_state(self, minimal_env):
        agent = P1Agent(_healthy_ha())
        state = await agent.collect()
        assert state.power_w == pytest.approx(244.0)

    async def test_import_kwh_is_sum_of_tariffs(self, minimal_env):
        agent = P1Agent(_healthy_ha())
        state = await agent.collect()
        assert state.daily_import_kwh == pytest.approx(1234.5 + 567.8)

    async def test_export_kwh_is_sum_of_tariffs(self, minimal_env):
        agent = P1Agent(_healthy_ha())
        state = await agent.collect()
        assert state.daily_export_kwh == pytest.approx(8000.0 + 336.3)

    async def test_status_online_when_all_entities_available(self, minimal_env):
        agent = P1Agent(_healthy_ha())
        state = await agent.collect()
        assert state.status == DeviceStatus.ONLINE

    async def test_status_offline_when_power_unavailable(self, minimal_env):
        ha = _make_ha({
            # power entity missing → unavailable
            "sensor.p1_meter_energie_import_tarief_1": "1000.0",
            "sensor.p1_meter_energie_import_tarief_2": "500.0",
            "sensor.p1_meter_energie_export_tarief_1": "500.0",
            "sensor.p1_meter_energie_export_tarief_2": "100.0",
        })
        agent = P1Agent(ha)
        state = await agent.collect()
        assert state.status == DeviceStatus.OFFLINE
        assert state.power_w == pytest.approx(0.0)  # fallback

    async def test_negative_power_is_injecting(self, minimal_env):
        ha = _make_ha({
            "sensor.p1_meter_vermogen": "-1200",
            "sensor.p1_meter_energie_import_tarief_1": "1000.0",
            "sensor.p1_meter_energie_import_tarief_2": "500.0",
            "sensor.p1_meter_energie_export_tarief_1": "500.0",
            "sensor.p1_meter_energie_export_tarief_2": "100.0",
        })
        agent = P1Agent(ha)
        state = await agent.collect()
        assert state.power_w == pytest.approx(-1200.0)
        assert state.surplus_w == pytest.approx(1200.0)

    async def test_surplus_is_zero_when_consuming(self, minimal_env):
        agent = P1Agent(_healthy_ha())
        state = await agent.collect()
        assert state.surplus_w == pytest.approx(0.0)

    async def test_all_entities_missing_returns_zeros(self, minimal_env):
        agent = P1Agent(_make_ha({}))
        state = await agent.collect()
        assert state.power_w == pytest.approx(0.0)
        assert state.daily_import_kwh == pytest.approx(0.0)
        assert state.daily_export_kwh == pytest.approx(0.0)
        assert state.status == DeviceStatus.OFFLINE
