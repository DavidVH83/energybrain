"""Tests for energybrain.agents.base_agent."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from energybrain.agents.base_agent import BaseAgent
from energybrain.exceptions import AgentError, HAConnectionError, HAStateUnavailableError
from energybrain.models import DeviceStatus
from energybrain.utils.ha_client import HAClient


# ---------------------------------------------------------------------------
# Minimal concrete agent for testing BaseAgent behaviour
# ---------------------------------------------------------------------------

class _FakeState:
    def __init__(self, value: float):
        self.value = value


class _ConcreteAgent(BaseAgent[_FakeState]):
    """Minimal concrete subclass for testing BaseAgent."""
    AGENT_NAME = "concrete_test_agent"

    async def collect(self) -> _FakeState:
        value, _ = await self._get_float("sensor.test", fallback=0.0)
        return _FakeState(value)


def _make_ha(state_value="100", state_key="state") -> HAClient:
    ha = MagicMock(spec=HAClient)
    ha.get_state = AsyncMock(return_value={"state": state_value, "attributes": {}})
    ha.get_attribute = AsyncMock(return_value=None)
    ha.call_service = AsyncMock(return_value=[])
    return ha


# ---------------------------------------------------------------------------
# collect()
# ---------------------------------------------------------------------------

class TestCollect:
    async def test_collect_returns_state_on_success(self):
        ha = _make_ha("3500.0")
        agent = _ConcreteAgent(ha)
        state = await agent.collect()
        assert state.value == pytest.approx(3500.0)

    async def test_collect_uses_fallback_when_unavailable(self):
        ha = MagicMock(spec=HAClient)
        ha.get_state = AsyncMock(side_effect=HAStateUnavailableError("sensor.test"))
        agent = _ConcreteAgent(ha)
        state = await agent.collect()
        assert state.value == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# _get_float
# ---------------------------------------------------------------------------

class TestGetFloat:
    async def test_returns_float_and_online(self):
        ha = _make_ha("42.5")
        agent = _ConcreteAgent(ha)
        value, status = await agent._get_float("sensor.foo")
        assert value == pytest.approx(42.5)
        assert status == DeviceStatus.ONLINE

    async def test_returns_fallback_and_offline_when_unavailable(self):
        ha = MagicMock(spec=HAClient)
        ha.get_state = AsyncMock(side_effect=HAStateUnavailableError("sensor.foo"))
        agent = _ConcreteAgent(ha)
        value, status = await agent._get_float("sensor.foo", fallback=99.0)
        assert value == pytest.approx(99.0)
        assert status == DeviceStatus.OFFLINE

    async def test_returns_fallback_and_error_on_parse_error(self):
        ha = _make_ha("not_a_number")
        agent = _ConcreteAgent(ha)
        value, status = await agent._get_float("sensor.foo", fallback=-1.0)
        assert value == pytest.approx(-1.0)
        assert status == DeviceStatus.ERROR

    async def test_default_fallback_is_zero(self):
        ha = MagicMock(spec=HAClient)
        ha.get_state = AsyncMock(side_effect=HAStateUnavailableError("sensor.foo"))
        agent = _ConcreteAgent(ha)
        value, _ = await agent._get_float("sensor.foo")
        assert value == 0.0


# ---------------------------------------------------------------------------
# _get_str
# ---------------------------------------------------------------------------

class TestGetStr:
    async def test_returns_string_and_online(self):
        ha = _make_ha("heat")
        agent = _ConcreteAgent(ha)
        value, status = await agent._get_str("sensor.hvac_mode")
        assert value == "heat"
        assert status == DeviceStatus.ONLINE

    async def test_returns_fallback_and_offline_when_unavailable(self):
        ha = MagicMock(spec=HAClient)
        ha.get_state = AsyncMock(side_effect=HAStateUnavailableError("sensor.foo"))
        agent = _ConcreteAgent(ha)
        value, status = await agent._get_str("sensor.foo", fallback="unknown_fallback")
        assert value == "unknown_fallback"
        assert status == DeviceStatus.OFFLINE

    async def test_default_fallback_is_empty_string(self):
        ha = MagicMock(spec=HAClient)
        ha.get_state = AsyncMock(side_effect=HAStateUnavailableError("sensor.foo"))
        agent = _ConcreteAgent(ha)
        value, _ = await agent._get_str("sensor.foo")
        assert value == ""


# ---------------------------------------------------------------------------
# _get_bool
# ---------------------------------------------------------------------------

class TestGetBool:
    @pytest.mark.parametrize("raw,expected", [
        ("on", True), ("off", False),
        ("true", True), ("false", False),
        ("1", True), ("0", False),
        ("ON", True), ("OFF", False),
    ])
    async def test_interprets_ha_bool_states(self, raw, expected):
        ha = _make_ha(raw)
        agent = _ConcreteAgent(ha)
        value, status = await agent._get_bool("binary_sensor.foo")
        assert value is expected
        assert status == DeviceStatus.ONLINE

    async def test_returns_fallback_and_offline_when_unavailable(self):
        ha = MagicMock(spec=HAClient)
        ha.get_state = AsyncMock(side_effect=HAStateUnavailableError("binary_sensor.foo"))
        agent = _ConcreteAgent(ha)
        value, status = await agent._get_bool("binary_sensor.foo", fallback=True)
        assert value is True
        assert status == DeviceStatus.OFFLINE


# ---------------------------------------------------------------------------
# _get_attribute
# ---------------------------------------------------------------------------

class TestGetAttribute:
    async def test_returns_attribute_value(self):
        ha = MagicMock(spec=HAClient)
        ha.get_attribute = AsyncMock(return_value="heating")
        agent = _ConcreteAgent(ha)
        result = await agent._get_attribute("climate.anna", "hvac_action")
        assert result == "heating"

    async def test_returns_fallback_on_unavailable(self):
        ha = MagicMock(spec=HAClient)
        ha.get_attribute = AsyncMock(side_effect=HAStateUnavailableError("climate.anna"))
        agent = _ConcreteAgent(ha)
        result = await agent._get_attribute("climate.anna", "hvac_action", fallback="idle")
        assert result == "idle"

    async def test_returns_fallback_on_connection_error(self):
        ha = MagicMock(spec=HAClient)
        ha.get_attribute = AsyncMock(side_effect=HAConnectionError("down"))
        agent = _ConcreteAgent(ha)
        result = await agent._get_attribute("climate.anna", "hvac_action", fallback="idle")
        assert result == "idle"

    async def test_default_fallback_is_none(self):
        ha = MagicMock(spec=HAClient)
        ha.get_attribute = AsyncMock(side_effect=HAStateUnavailableError("x"))
        agent = _ConcreteAgent(ha)
        result = await agent._get_attribute("x", "y")
        assert result is None


# ---------------------------------------------------------------------------
# _call_service
# ---------------------------------------------------------------------------

class TestCallService:
    async def test_calls_ha_service_with_correct_params(self):
        ha = MagicMock(spec=HAClient)
        ha.call_service = AsyncMock(return_value=[])
        agent = _ConcreteAgent(ha)
        await agent._call_service("climate", "set_temperature", "climate.anna", temperature=21.0)
        ha.call_service.assert_awaited_once_with(
            "climate", "set_temperature",
            entity_id="climate.anna",
            temperature=21.0,
        )

    async def test_raises_agent_error_on_connection_failure(self):
        ha = MagicMock(spec=HAClient)
        ha.call_service = AsyncMock(side_effect=HAConnectionError("down"))
        agent = _ConcreteAgent(ha)
        with pytest.raises(AgentError) as exc_info:
            await agent._call_service("climate", "set_temperature", "climate.anna")
        assert exc_info.value.agent_name == "concrete_test_agent"


# ---------------------------------------------------------------------------
# _determine_status
# ---------------------------------------------------------------------------

class TestDetermineStatus:
    def setup_method(self):
        ha = _make_ha()
        self.agent = _ConcreteAgent(ha)

    def test_all_online_returns_online(self):
        result = self.agent._determine_status(DeviceStatus.ONLINE, DeviceStatus.ONLINE)
        assert result == DeviceStatus.ONLINE

    def test_any_error_returns_error(self):
        result = self.agent._determine_status(DeviceStatus.ONLINE, DeviceStatus.ERROR)
        assert result == DeviceStatus.ERROR

    def test_any_offline_returns_offline(self):
        result = self.agent._determine_status(DeviceStatus.ONLINE, DeviceStatus.OFFLINE)
        assert result == DeviceStatus.OFFLINE

    def test_error_takes_priority_over_offline(self):
        result = self.agent._determine_status(DeviceStatus.OFFLINE, DeviceStatus.ERROR)
        assert result == DeviceStatus.ERROR

    def test_empty_returns_unknown(self):
        result = self.agent._determine_status()
        assert result == DeviceStatus.UNKNOWN

    def test_single_online(self):
        result = self.agent._determine_status(DeviceStatus.ONLINE)
        assert result == DeviceStatus.ONLINE


# ---------------------------------------------------------------------------
# _wrap_error
# ---------------------------------------------------------------------------

class TestWrapError:
    def test_wraps_exception_as_agent_error(self):
        ha = _make_ha()
        agent = _ConcreteAgent(ha)
        exc = agent._wrap_error(ValueError("bad"), "context message")
        assert isinstance(exc, AgentError)
        assert exc.agent_name == "concrete_test_agent"
        assert "context message" in exc.reason

    def test_wraps_without_context(self):
        ha = _make_ha()
        agent = _ConcreteAgent(ha)
        exc = agent._wrap_error(RuntimeError("boom"))
        assert isinstance(exc, AgentError)
        assert "boom" in exc.reason
