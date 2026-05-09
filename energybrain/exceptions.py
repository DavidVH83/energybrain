"""EnergyBrain custom exceptions."""


class EnergyBrainError(Exception):
    """Base exception for all EnergyBrain errors."""


class ConfigError(EnergyBrainError):
    """Raised when required configuration is missing or invalid."""


class HAConnectionError(EnergyBrainError):
    """Raised when Home Assistant connection fails."""


class HAStateUnavailableError(EnergyBrainError):
    """Raised when a required HA entity state is unavailable."""

    def __init__(self, entity_id: str) -> None:
        self.entity_id = entity_id
        super().__init__(f"Entity {entity_id!r} state is unavailable")


class SafetyLimitError(EnergyBrainError):
    """Raised when an action would violate a hard safety limit."""

    def __init__(self, limit_name: str, current: float, threshold: float) -> None:
        self.limit_name = limit_name
        self.current = current
        self.threshold = threshold
        super().__init__(
            f"Safety limit {limit_name!r} violated: current={current}, threshold={threshold}"
        )


class StubActionError(EnergyBrainError):
    """Raised when a write action is blocked because MARSTEK_WRITE_ENABLED=false."""


class DatabaseError(EnergyBrainError):
    """Raised when a database operation fails."""


class AgentError(EnergyBrainError):
    """Raised when an agent fails to collect data."""

    def __init__(self, agent_name: str, reason: str) -> None:
        self.agent_name = agent_name
        self.reason = reason
        super().__init__(f"Agent {agent_name!r} failed: {reason}")


class RetryExhaustedError(EnergyBrainError):
    """Raised when all retry attempts are exhausted."""

    def __init__(self, attempts: int, last_error: Exception) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"All {attempts} retry attempts exhausted. Last error: {last_error}"
        )
