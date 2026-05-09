"""HAControlAgent — reads/writes EnergyBrain control state via HA input helpers.

Bridges the HA dashboard controls (input_boolean, input_select, input_number,
input_datetime, input_text) to the ControlState model used by DayPlanner.
Called every cycle: read at start, write status at end.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from energybrain.agents.base_agent import BaseAgent
from energybrain.exceptions import HAStateUnavailableError
from energybrain.models import ControlState
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

_BRAIN_ENABLED = "input_boolean.energybrain_enabled"
_BRAIN_MODE = "input_select.energybrain_mode"
_VACATION_ACTIVE = "input_boolean.energybrain_vacation_mode"
_VACATION_START = "input_datetime.energybrain_vacation_start"
_VACATION_END = "input_datetime.energybrain_vacation_end"
_DHW_BOOST_NOW = "input_boolean.energybrain_dhw_boost_now"
_DHW_TARGET_TEMP = "input_number.energybrain_dhw_target_temp"

_STATUS_TEXT = "input_text.energybrain_status"
_LAST_ACTION = "input_text.energybrain_last_action"
_TODAY_PLAN = "input_text.energybrain_today_plan"
_NEXT_ACTION = "input_text.energybrain_next_action"


class HAControlAgent(BaseAgent[ControlState]):
    """Reads HA input helpers and exposes them as ControlState."""

    AGENT_NAME = "ha_control_agent"

    async def collect(self) -> ControlState:
        return await self.get_control_state()

    async def get_control_state(self) -> ControlState:
        """Read all input helpers and return a ControlState snapshot."""
        brain_enabled, _ = await self._get_bool(_BRAIN_ENABLED, fallback=True)
        brain_mode, _ = await self._get_str(_BRAIN_MODE, fallback="auto")
        vacation_active, _ = await self._get_bool(_VACATION_ACTIVE, fallback=False)
        dhw_boost_now, _ = await self._get_bool(_DHW_BOOST_NOW, fallback=False)
        dhw_target_temp, _ = await self._get_float(_DHW_TARGET_TEMP, fallback=55.0)
        vacation_start = await self._get_datetime(_VACATION_START)
        vacation_end = await self._get_datetime(_VACATION_END)

        return ControlState(
            brain_enabled=brain_enabled,
            brain_mode=brain_mode,
            vacation_active=vacation_active,
            vacation_start=vacation_start,
            vacation_end=vacation_end,
            dhw_boost_now=dhw_boost_now,
            dhw_target_temp=dhw_target_temp,
        )

    async def update_status(
        self,
        status: str,
        last_action: str,
        today_plan: str,
        next_action: str,
    ) -> None:
        """Write EnergyBrain status back to HA input_text helpers.

        Visible on the HA dashboard. Called at the end of every cycle.
        """
        for entity, value in (
            (_STATUS_TEXT, status),
            (_LAST_ACTION, last_action),
            (_TODAY_PLAN, today_plan),
            (_NEXT_ACTION, next_action),
        ):
            await self._call_service("input_text", "set_value", entity, value=value)

    async def _get_datetime(self, entity_id: str) -> Optional[datetime]:
        """Parse an input_datetime entity into a Python datetime, or None."""
        try:
            raw = await self._ha.get_state(entity_id)
            state = raw.get("state", "")
            if state and state not in ("unknown", "unavailable", ""):
                return datetime.fromisoformat(state)
        except (HAStateUnavailableError, ValueError, KeyError):
            pass
        return None
