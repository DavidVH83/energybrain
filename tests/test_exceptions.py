"""Tests for energybrain.exceptions."""
import pytest

from energybrain.exceptions import (
    AgentError,
    ConfigError,
    DatabaseError,
    EnergyBrainError,
    HAConnectionError,
    HAStateUnavailableError,
    RetryExhaustedError,
    SafetyLimitError,
    StubActionError,
)


class TestExceptionHierarchy:
    def test_config_error_is_energybrain_error(self):
        assert issubclass(ConfigError, EnergyBrainError)

    def test_ha_connection_error_is_energybrain_error(self):
        assert issubclass(HAConnectionError, EnergyBrainError)

    def test_ha_state_unavailable_is_energybrain_error(self):
        assert issubclass(HAStateUnavailableError, EnergyBrainError)

    def test_safety_limit_is_energybrain_error(self):
        assert issubclass(SafetyLimitError, EnergyBrainError)

    def test_stub_action_is_energybrain_error(self):
        assert issubclass(StubActionError, EnergyBrainError)

    def test_database_error_is_energybrain_error(self):
        assert issubclass(DatabaseError, EnergyBrainError)

    def test_agent_error_is_energybrain_error(self):
        assert issubclass(AgentError, EnergyBrainError)

    def test_retry_exhausted_is_energybrain_error(self):
        assert issubclass(RetryExhaustedError, EnergyBrainError)


class TestHAStateUnavailableError:
    def test_stores_entity_id(self):
        exc = HAStateUnavailableError("sensor.goodwe_power")
        assert exc.entity_id == "sensor.goodwe_power"

    def test_message_contains_entity_id(self):
        exc = HAStateUnavailableError("sensor.marstek_soc")
        assert "sensor.marstek_soc" in str(exc)


class TestSafetyLimitError:
    def test_stores_attributes(self):
        exc = SafetyLimitError("indoor_temp_min_winter", 16.8, 17.0)
        assert exc.limit_name == "indoor_temp_min_winter"
        assert exc.current == 16.8
        assert exc.threshold == 17.0

    def test_message_contains_limit_name(self):
        exc = SafetyLimitError("hvac_max_setpoint", 23.0, 22.5)
        assert "hvac_max_setpoint" in str(exc)


class TestAgentError:
    def test_stores_agent_name_and_reason(self):
        exc = AgentError("goodwe_agent", "HTTP 503")
        assert exc.agent_name == "goodwe_agent"
        assert exc.reason == "HTTP 503"

    def test_message_format(self):
        exc = AgentError("p1_agent", "Connection refused")
        assert "p1_agent" in str(exc)
        assert "Connection refused" in str(exc)


class TestRetryExhaustedError:
    def test_stores_attempts_and_last_error(self):
        cause = ConnectionError("timeout")
        exc = RetryExhaustedError(3, cause)
        assert exc.attempts == 3
        assert exc.last_error is cause

    def test_message_contains_attempts(self):
        exc = RetryExhaustedError(5, RuntimeError("x"))
        assert "5" in str(exc)

    def test_is_catchable_as_energybrain_error(self):
        exc = RetryExhaustedError(1, RuntimeError("x"))
        with pytest.raises(EnergyBrainError):
            raise exc
