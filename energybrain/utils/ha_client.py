"""Home Assistant REST API client.

All HA communication goes through this class.
Uses exponential backoff retry for every outbound request.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import aiohttp

from energybrain.exceptions import HAConnectionError, HAStateUnavailableError
from energybrain.utils.logging_config import get_logger
from energybrain.utils.retry import retry_async

logger = get_logger(__name__)

# Timeout for a single HA API call (connect + read)
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class HAClient:
    """Async client for the Home Assistant REST API.

    Usage::

        async with HAClient(url="http://homeassistant.local:8123", token="...") as client:
            state = await client.get_state("sensor.goodwe_pv_power")
            await client.call_service("climate", "set_temperature",
                                      entity_id="climate.anna", temperature=21.0)
    """

    def __init__(self, url: str, token: str) -> None:
        self._base = url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "HAClient":
        await self.open()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    async def open(self) -> None:
        """Open the underlying aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self._headers,
                timeout=_REQUEST_TIMEOUT,
            )

    async def close(self) -> None:
        """Close the underlying aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        """Fetch the current state of a single HA entity.

        Args:
            entity_id: HA entity ID (e.g. ``sensor.goodwe_pv_power``).

        Returns:
            HA state dict with keys: ``state``, ``attributes``,
            ``last_changed``, ``last_updated``.

        Raises:
            HAStateUnavailableError: If the entity state is ``unavailable``
                or ``unknown``.
            HAConnectionError: On network / HTTP errors after all retries.
        """
        result: dict[str, Any] = await retry_async(
            self._get_state_once,
            entity_id,
            retryable_exceptions=(aiohttp.ClientError, asyncio.TimeoutError),
        )
        if result["state"] in ("unavailable", "unknown"):
            raise HAStateUnavailableError(entity_id)
        return result

    async def get_state_raw(self, entity_id: str) -> dict[str, Any]:
        """Like :meth:`get_state` but does NOT raise for unavailable/unknown.

        Returns:
            HA state dict. Caller is responsible for checking ``state`` value.
        """
        return await retry_async(
            self._get_state_once,
            entity_id,
            retryable_exceptions=(aiohttp.ClientError, asyncio.TimeoutError),
        )

    async def get_states(self, entity_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Fetch states for multiple entities concurrently.

        Unavailable / unknown states are included as-is (no exception raised).
        Individual fetch errors are logged and the entity is omitted from results.

        Args:
            entity_ids: List of HA entity IDs.

        Returns:
            Dict mapping entity_id → raw HA state dict (only for successful fetches).
        """
        tasks = [self.get_state_raw(eid) for eid in entity_ids]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        out: dict[str, dict[str, Any]] = {}
        for eid, result in zip(entity_ids, raw_results):
            if isinstance(result, Exception):
                logger.warning("get_states_partial_failure", entity_id=eid, error=str(result))
            else:
                out[eid] = result
        return out

    async def call_service(
        self,
        domain: str,
        service: str,
        **service_data: Any,
    ) -> list[dict[str, Any]]:
        """Call a HA service.

        Args:
            domain: HA domain (e.g. ``climate``, ``select``, ``number``).
            service: Service name (e.g. ``set_temperature``, ``select_option``).
            **service_data: Keyword arguments become the service data payload.
                            ``entity_id`` must be included if the service targets
                            a specific entity.

        Returns:
            List of affected HA state dicts (as returned by HA).

        Raises:
            HAConnectionError: On network / HTTP errors after all retries.
        """
        return await retry_async(
            self._call_service_once,
            domain,
            service,
            service_data,
            retryable_exceptions=(aiohttp.ClientError, asyncio.TimeoutError),
        )

    async def get_attribute(self, entity_id: str, attribute: str) -> Any:
        """Return a single attribute from an entity's state.

        Args:
            entity_id: HA entity ID.
            attribute: Attribute name (e.g. ``temperature``, ``hvac_action``).

        Returns:
            Attribute value, or ``None`` if the attribute is absent.

        Raises:
            HAStateUnavailableError: If the entity is unavailable.
            HAConnectionError: On network errors.
        """
        state = await self.get_state(entity_id)
        return state.get("attributes", {}).get(attribute)

    async def ping(self) -> bool:
        """Check if HA is reachable and the token is valid.

        Returns:
            True if HA responds with HTTP 200 to the ``/api/`` endpoint.
        """
        try:
            await self._request("GET", "/api/")
            return True
        except HAConnectionError:
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_state_once(self, entity_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/states/{entity_id}")

    async def _call_service_once(
        self,
        domain: str,
        service: str,
        data: dict[str, Any],
    ) -> list[dict[str, Any]]:
        return await self._request("POST", f"/api/services/{domain}/{service}", json=data)

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Execute a single HTTP request against the HA API.

        Raises:
            HAConnectionError: On HTTP errors or network failures.
        """
        if self._session is None or self._session.closed:
            raise HAConnectionError("HAClient session is not open — call open() first")

        url = f"{self._base}{path}"
        try:
            async with self._session.request(method, url, **kwargs) as resp:
                if resp.status == 401:
                    raise HAConnectionError("HA authentication failed — check HA_TOKEN")
                if resp.status == 404:
                    raise HAConnectionError(f"HA entity or endpoint not found: {path}")
                if resp.status >= 400:
                    body = await resp.text()
                    raise HAConnectionError(
                        f"HA API returned HTTP {resp.status} for {path}: {body[:200]}"
                    )
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise HAConnectionError(f"Network error reaching HA at {url}: {exc}") from exc
        except asyncio.TimeoutError as exc:
            raise HAConnectionError(f"Timeout reaching HA at {url}") from exc
