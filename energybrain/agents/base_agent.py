"""Abstract base class for all EnergyBrain data agents.

Every agent that reads from or writes to Home Assistant inherits from BaseAgent.
BaseAgent owns the HAClient reference and provides shared helpers for
safe state reading, unavailability handling, and structured logging.
"""
from __future__ import annotations

import abc
from typing import Any, Generic, Optional, TypeVar

from energybrain.exceptions import AgentError, HAConnectionError, HAStateUnavailableError
from energybrain.models import DeviceStatus
from energybrain.utils.ha_client import HAClient
from energybrain.utils.logging_config import get_logger

T = TypeVar("T")

logger = get_logger(__name__)


class BaseAgent(abc.ABC, Generic[T]):
    """Abstract base for all EnergyBrain HA agents.

    Subclasses implement :meth:`collect` which returns a typed state object.

    Lifecycle::

        agent = MyAgent(ha_client)
        state = await agent.collect()   # Returns MyState dataclass
    """

    #: Override in subclass — used for log context and AgentError messages.
    AGENT_NAME: str = "base_agent"

    def __init__(self, ha: HAClient) -> None:
        self._ha = ha
        self._log = get_logger(self.AGENT_NAME)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def collect(self) -> T:
        """Collect and return the current device state.

        Returns:
            A typed state dataclass specific to this agent.

        Raises:
            AgentError: If data collection fails after all retries.
        """

    # ------------------------------------------------------------------
    # Safe state helpers — handle unavailable/unknown gracefully
    # ------------------------------------------------------------------

    async def _get_float(
        self,
        entity_id: str,
        fallback: float = 0.0,
    ) -> tuple[float, DeviceStatus]:
        """Read a numeric sensor, returning (value, status).

        Returns ``(fallback, DeviceStatus.OFFLINE)`` when the entity is
        unavailable (e.g. GoodWe in sleep mode at night).

        Args:
            entity_id: HA entity ID.
            fallback: Value to use when the entity is unavailable.

        Returns:
            Tuple of (float value, DeviceStatus).
        """
        try:
            raw = await self._ha.get_state(entity_id)
            return float(raw["state"]), DeviceStatus.ONLINE
        except HAStateUnavailableError:
            self._log.debug("entity_unavailable_using_fallback", entity=entity_id, fallback=fallback)
            return fallback, DeviceStatus.OFFLINE
        except (ValueError, TypeError) as exc:
            self._log.warning("entity_parse_error", entity=entity_id, error=str(exc))
            return fallback, DeviceStatus.ERROR

    async def _get_str(
        self,
        entity_id: str,
        fallback: str = "",
    ) -> tuple[str, DeviceStatus]:
        """Read a string / enum sensor state, returning (value, status).

        Args:
            entity_id: HA entity ID.
            fallback: Value to use when the entity is unavailable.

        Returns:
            Tuple of (string value, DeviceStatus).
        """
        try:
            raw = await self._ha.get_state(entity_id)
            return str(raw["state"]), DeviceStatus.ONLINE
        except HAStateUnavailableError:
            self._log.debug("entity_unavailable_using_fallback", entity=entity_id, fallback=fallback)
            return fallback, DeviceStatus.OFFLINE

    async def _get_bool(
        self,
        entity_id: str,
        fallback: bool = False,
    ) -> tuple[bool, DeviceStatus]:
        """Read a binary_sensor state, returning (value, status).

        Interprets HA ``on``/``off``/``true``/``false``/``1``/``0``.

        Args:
            entity_id: HA entity ID.
            fallback: Value to use when the entity is unavailable.

        Returns:
            Tuple of (bool value, DeviceStatus).
        """
        try:
            raw = await self._ha.get_state(entity_id)
            return raw["state"].lower() in ("on", "true", "1"), DeviceStatus.ONLINE
        except HAStateUnavailableError:
            self._log.debug("entity_unavailable_using_fallback", entity=entity_id, fallback=fallback)
            return fallback, DeviceStatus.OFFLINE

    async def _get_attribute(
        self,
        entity_id: str,
        attribute: str,
        fallback: Any = None,
    ) -> Any:
        """Read a specific attribute from an entity.

        Returns ``fallback`` silently if the entity is unavailable or the
        attribute is absent.

        Args:
            entity_id: HA entity ID.
            attribute: Attribute key.
            fallback: Value when entity is unavailable or attribute missing.

        Returns:
            Attribute value or fallback.
        """
        try:
            return await self._ha.get_attribute(entity_id, attribute)
        except (HAStateUnavailableError, HAConnectionError):
            return fallback

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    async def _call_service(
        self,
        domain: str,
        service: str,
        entity_id: str,
        **extra: Any,
    ) -> None:
        """Call a HA service and log the result.

        Args:
            domain: HA domain (e.g. ``climate``, ``select``).
            service: Service name (e.g. ``set_temperature``).
            entity_id: Target entity.
            **extra: Additional service data fields.

        Raises:
            AgentError: If the service call fails.
        """
        try:
            await self._ha.call_service(domain, service, entity_id=entity_id, **extra)
            self._log.info(
                "service_called",
                domain=domain,
                service=service,
                entity=entity_id,
                data=extra,
            )
        except HAConnectionError as exc:
            raise AgentError(
                self.AGENT_NAME,
                f"Service call {domain}.{service} on {entity_id} failed: {exc}",
            ) from exc

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def _determine_status(self, *statuses: DeviceStatus) -> DeviceStatus:
        """Reduce multiple per-entity statuses to a single device status.

        Returns ONLINE only if all are ONLINE, ERROR if any is ERROR,
        OFFLINE if any is OFFLINE (but none ERROR), else UNKNOWN.

        Args:
            *statuses: Individual entity statuses.

        Returns:
            Aggregated DeviceStatus.
        """
        if not statuses:
            return DeviceStatus.UNKNOWN
        if DeviceStatus.ERROR in statuses:
            return DeviceStatus.ERROR
        if DeviceStatus.OFFLINE in statuses:
            return DeviceStatus.OFFLINE
        if all(s == DeviceStatus.ONLINE for s in statuses):
            return DeviceStatus.ONLINE
        return DeviceStatus.UNKNOWN

    # ------------------------------------------------------------------
    # Error wrapping
    # ------------------------------------------------------------------

    def _wrap_error(self, exc: Exception, context: str = "") -> AgentError:
        """Wrap any exception as an AgentError with agent context.

        Args:
            exc: The original exception.
            context: Optional human-readable context string.

        Returns:
            AgentError ready to raise.
        """
        msg = f"{context}: {exc}" if context else str(exc)
        return AgentError(self.AGENT_NAME, msg)
