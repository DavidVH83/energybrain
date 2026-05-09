"""Tests for energybrain.agents.goodwe_agent."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from energybrain.agents.goodwe_agent import GoodWeAgent
from energybrain.exceptions import HAStateUnavailableError
from energybrain.models import DeviceStatus
from energybrain.utils.ha_client import HAClient


def _make_ha(states: dict[str, str] | None = None) -> HAClient:
    ha = MagicMock(spec=HAClient)
    states = states or {}

    async def _get_state(entity_id: str) -> dict:
        if entity_id not in states:
            raise HAStateUnavailableError(entity_id)
        return {"state": states[entity_id], "attributes": {}}

    ha.get_state = AsyncMock(side_effect=_get_state)
    return ha


def _daytime_ha() -> HAClient:
    return _make_ha({
        "sensor.goodwe_pv_power": "3500",
        "sensor.goodwe_today_s_pv_generation": "12.5",
    })


def _nighttime_ha() -> HAClient:
    # pv_power unavailable at night, daily total still readable
    return _make_ha({"sensor.goodwe_today_s_pv_generation": "12.5"})


class TestGoodWeAgentCollect:
    async def test_daytime_returns_correct_power(self, minimal_env):
        agent = GoodWeAgent(_daytime_ha())
        state = await agent.collect()
        assert state.power_w == pytest.approx(3500.0)

    async def test_daytime_returns_correct_daily_kwh(self, minimal_env):
        agent = GoodWeAgent(_daytime_ha())
        state = await agent.collect()
        assert state.daily_energy_kwh == pytest.approx(12.5)

    async def test_daytime_status_is_online(self, minimal_env):
        agent = GoodWeAgent(_daytime_ha())
        state = await agent.collect()
        assert state.status == DeviceStatus.ONLINE

    async def test_nighttime_power_is_zero(self, minimal_env):
        agent = GoodWeAgent(_nighttime_ha())
        state = await agent.collect()
        assert state.power_w == pytest.approx(0.0)

    async def test_nighttime_daily_kwh_preserved(self, minimal_env):
        agent = GoodWeAgent(_nighttime_ha())
        state = await agent.collect()
        assert state.daily_energy_kwh == pytest.approx(12.5)

    async def test_nighttime_status_is_online(self, minimal_env):
        """Night sleep (pv offline, daily online) must be ONLINE — device is healthy."""
        agent = GoodWeAgent(_nighttime_ha())
        state = await agent.collect()
        assert state.status == DeviceStatus.ONLINE

    async def test_both_unavailable_is_offline(self, minimal_env):
        agent = GoodWeAgent(_make_ha({}))
        state = await agent.collect()
        assert state.status == DeviceStatus.OFFLINE

    async def test_zero_production_during_night(self, minimal_env):
        agent = GoodWeAgent(_nighttime_ha())
        state = await agent.collect()
        # No error raised — normal night behaviour
        assert state.power_w >= 0.0
