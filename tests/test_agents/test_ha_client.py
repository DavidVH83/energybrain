"""Tests for energybrain.utils.ha_client."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from energybrain.exceptions import HAConnectionError, HAStateUnavailableError
from energybrain.utils.ha_client import HAClient

BASE_URL = "http://homeassistant.local:8123"
TOKEN = "test-token-abc"


def _make_client() -> HAClient:
    return HAClient(url=BASE_URL, token=TOKEN)


def _mock_response(status: int, json_data: object) -> MagicMock:
    """Build a mock aiohttp response object."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data)
    resp.text = AsyncMock(return_value=str(json_data))
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _mock_session(response: MagicMock) -> MagicMock:
    session = MagicMock()
    session.closed = False
    session.close = AsyncMock()
    session.request = MagicMock(return_value=response)
    return session


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

class TestSessionLifecycle:
    async def test_open_creates_session(self):
        client = _make_client()
        await client.open()
        assert client._session is not None
        await client.close()

    async def test_close_clears_session(self):
        client = _make_client()
        await client.open()
        await client.close()
        assert client._session is None

    async def test_context_manager_opens_and_closes(self):
        client = _make_client()
        async with client:
            assert client._session is not None
        assert client._session is None

    async def test_double_open_is_safe(self):
        client = _make_client()
        await client.open()
        await client.open()   # Should not raise
        await client.close()

    async def test_double_close_is_safe(self):
        client = _make_client()
        await client.open()
        await client.close()
        await client.close()  # Should not raise


# ---------------------------------------------------------------------------
# get_state
# ---------------------------------------------------------------------------

class TestGetState:
    async def test_returns_state_dict_on_success(self):
        state_data = {
            "entity_id": "sensor.goodwe_pv_power",
            "state": "3500",
            "attributes": {"unit_of_measurement": "W"},
            "last_changed": "2026-05-09T10:00:00+00:00",
        }
        client = _make_client()
        client._session = _mock_session(_mock_response(200, state_data))
        result = await client.get_state("sensor.goodwe_pv_power")
        assert result["state"] == "3500"
        assert result["attributes"]["unit_of_measurement"] == "W"

    async def test_raises_unavailable_when_state_is_unavailable(self):
        state_data = {"entity_id": "sensor.foo", "state": "unavailable", "attributes": {}}
        client = _make_client()
        client._session = _mock_session(_mock_response(200, state_data))
        with pytest.raises(HAStateUnavailableError) as exc_info:
            await client.get_state("sensor.foo")
        assert exc_info.value.entity_id == "sensor.foo"

    async def test_raises_unavailable_when_state_is_unknown(self):
        state_data = {"entity_id": "sensor.bar", "state": "unknown", "attributes": {}}
        client = _make_client()
        client._session = _mock_session(_mock_response(200, state_data))
        with pytest.raises(HAStateUnavailableError):
            await client.get_state("sensor.bar")

    async def test_raises_ha_connection_error_on_401(self):
        client = _make_client()
        client._session = _mock_session(_mock_response(401, {}))
        with pytest.raises(HAConnectionError, match="authentication"):
            await client.get_state("sensor.foo")

    async def test_raises_ha_connection_error_on_404(self):
        client = _make_client()
        client._session = _mock_session(_mock_response(404, {}))
        with pytest.raises(HAConnectionError, match="not found"):
            await client.get_state("sensor.missing")

    async def test_raises_ha_connection_error_on_500(self):
        client = _make_client()
        client._session = _mock_session(_mock_response(500, "internal error"))
        with pytest.raises(HAConnectionError, match="HTTP 500"):
            await client.get_state("sensor.foo")

    async def test_raises_ha_connection_error_on_session_not_open(self):
        client = _make_client()   # session is None
        with pytest.raises(HAConnectionError, match="not open"):
            await client._request("GET", "/api/states/sensor.foo")

    async def test_raises_ha_connection_error_on_client_error(self):
        client = _make_client()
        session = MagicMock()
        session.closed = False
        session.request = MagicMock(side_effect=aiohttp.ClientConnectorError(
            MagicMock(), OSError("connection refused")
        ))
        client._session = session
        with pytest.raises(HAConnectionError, match="Network error"):
            await client._request("GET", "/api/states/sensor.foo")


# ---------------------------------------------------------------------------
# get_state_raw
# ---------------------------------------------------------------------------

class TestGetStateRaw:
    async def test_does_not_raise_for_unavailable(self):
        state_data = {"entity_id": "sensor.foo", "state": "unavailable", "attributes": {}}
        client = _make_client()
        client._session = _mock_session(_mock_response(200, state_data))
        result = await client.get_state_raw("sensor.foo")
        assert result["state"] == "unavailable"


# ---------------------------------------------------------------------------
# get_states (batch)
# ---------------------------------------------------------------------------

class TestGetStates:
    async def test_returns_all_successful_states(self):
        state_a = {"entity_id": "sensor.a", "state": "100", "attributes": {}}
        state_b = {"entity_id": "sensor.b", "state": "200", "attributes": {}}

        call_count = 0
        responses = [state_a, state_b]

        async def fake_get_state_raw(entity_id):
            nonlocal call_count
            result = responses[call_count]
            call_count += 1
            return result

        client = _make_client()
        client.get_state_raw = fake_get_state_raw
        results = await client.get_states(["sensor.a", "sensor.b"])
        assert "sensor.a" in results
        assert results["sensor.a"]["state"] == "100"

    async def test_skips_failed_entities(self):
        async def fake_get_state_raw(entity_id):
            if entity_id == "sensor.bad":
                raise HAConnectionError("down")
            return {"entity_id": entity_id, "state": "42", "attributes": {}}

        client = _make_client()
        client.get_state_raw = fake_get_state_raw
        results = await client.get_states(["sensor.good", "sensor.bad"])
        assert "sensor.good" in results
        assert "sensor.bad" not in results


# ---------------------------------------------------------------------------
# call_service
# ---------------------------------------------------------------------------

class TestCallService:
    async def test_calls_correct_endpoint(self):
        response_data = [{"entity_id": "climate.anna", "state": "heat", "attributes": {}}]
        client = _make_client()
        session = MagicMock()
        session.closed = False

        captured = {}
        async def fake_request(method, url, **kwargs):
            captured["method"] = method
            captured["url"] = url
            captured["json"] = kwargs.get("json")
            resp = _mock_response(200, response_data)
            return resp.__aenter__.return_value

        session.request = MagicMock(side_effect=lambda m, u, **kw: _mock_response(200, response_data))
        client._session = session

        # Patch _call_service_once directly to inspect behaviour
        called_with = {}
        async def fake_once(domain, service, data):
            called_with.update({"domain": domain, "service": service, "data": data})
            return response_data

        client._call_service_once = fake_once
        result = await client.call_service("climate", "set_temperature", entity_id="climate.anna", temperature=21.0)
        assert called_with["domain"] == "climate"
        assert called_with["service"] == "set_temperature"
        assert called_with["data"]["temperature"] == 21.0

    async def test_raises_ha_connection_error_on_failure(self):
        client = _make_client()
        client._session = _mock_session(_mock_response(500, "fail"))
        with pytest.raises(HAConnectionError):
            await client.call_service("climate", "set_temperature", entity_id="climate.anna")


# ---------------------------------------------------------------------------
# get_attribute
# ---------------------------------------------------------------------------

class TestGetAttribute:
    async def test_returns_attribute_value(self):
        state_data = {
            "entity_id": "climate.anna",
            "state": "heat",
            "attributes": {"hvac_action": "heating", "temperature": 21.0},
        }
        client = _make_client()
        client._session = _mock_session(_mock_response(200, state_data))
        value = await client.get_attribute("climate.anna", "hvac_action")
        assert value == "heating"

    async def test_returns_none_for_missing_attribute(self):
        state_data = {
            "entity_id": "climate.anna",
            "state": "heat",
            "attributes": {},
        }
        client = _make_client()
        client._session = _mock_session(_mock_response(200, state_data))
        value = await client.get_attribute("climate.anna", "nonexistent")
        assert value is None


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------

class TestPing:
    async def test_ping_returns_true_on_success(self):
        client = _make_client()
        client._session = _mock_session(_mock_response(200, {"message": "API running."}))
        result = await client.ping()
        assert result is True

    async def test_ping_returns_false_on_connection_error(self):
        client = _make_client()
        async def fake_request(*a, **kw):
            raise HAConnectionError("down")
        client._request = fake_request
        result = await client.ping()
        assert result is False


# ---------------------------------------------------------------------------
# Authorization header
# ---------------------------------------------------------------------------

class TestAuthorization:
    async def test_bearer_token_in_headers(self):
        client = _make_client()
        await client.open()
        assert "Authorization" in client._headers
        assert client._headers["Authorization"] == f"Bearer {TOKEN}"
        await client.close()
