"""HomeConnectAgent — reads state of Siemens Home Connect appliances.

Handles: dishwasher, washing machine, dryer.
remote_start_allowed = True when binary_sensor.*_start_op_afstand is 'on'.
is_running = True when status sensor is in (run, delayedstart, pause).

Note on delay types:
  - Dishwasher: start_relative (number.vaatwasser_begin_relatief, unit=s, max=86400)
  - Washing machine / dryer: finish_relative (end time, unit uncertain — test in production)
"""
from __future__ import annotations

from energybrain.agents.base_agent import BaseAgent
from energybrain.models import ApplianceState, ApplianceType, DeviceStatus
from energybrain.utils.ha_client import HAClient
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

_RUNNING_STATUSES = {"run", "delayedstart", "pause"}

_APPLIANCE_CONFIG: dict[ApplianceType, dict] = {
    ApplianceType.DISHWASHER: {
        "remote_sensor": "binary_sensor.vaatwasser_start_op_afstand",
        "status_sensor": "sensor.vaatwasser_status",
        "power_switch": "switch.vaatwasser_inschakelen",
        "delay_entity": "number.vaatwasser_begin_relatief",
        "delay_type": "start_relative",
    },
    ApplianceType.WASHING_MACHINE: {
        "remote_sensor": "binary_sensor.wasmachine_start_op_afstand",
        "status_sensor": "sensor.wasmachine_status",
        "power_switch": "switch.wasmachine_inschakelen",
        "delay_entity": "number.wasmachine_relatieve_eindtijd",
        "delay_type": "finish_relative",
    },
    ApplianceType.DRYER: {
        "remote_sensor": "binary_sensor.droger_start_op_afstand",
        "status_sensor": "sensor.droger_status",
        "power_switch": "switch.droger_inschakelen",
        "delay_entity": "number.droger_relatieve_eindtijd",
        "delay_type": "finish_relative",
    },
}


class HomeConnectAgent(BaseAgent[dict]):
    """Reads current state for all three Home Connect appliances."""

    AGENT_NAME = "home_connect_agent"

    async def collect(self) -> dict[ApplianceType, ApplianceState]:
        result: dict[ApplianceType, ApplianceState] = {}
        for appliance_type in ApplianceType:
            result[appliance_type] = await self._collect_one(appliance_type)
        return result

    async def _collect_one(self, appliance_type: ApplianceType) -> ApplianceState:
        cfg = _APPLIANCE_CONFIG[appliance_type]
        remote_allowed, remote_st = await self._get_bool(cfg["remote_sensor"], fallback=False)
        status_str, status_st = await self._get_str(cfg["status_sensor"], fallback="inactive")
        is_running = status_str.lower() in _RUNNING_STATUSES

        # If remote sensor is unavailable, the appliance is offline/disconnected
        status = remote_st if remote_st != DeviceStatus.UNKNOWN else status_st

        self._log.debug(
            "appliance_collected",
            appliance=appliance_type.value,
            remote_allowed=remote_allowed,
            is_running=is_running,
            status=status.value,
        )
        return ApplianceState(
            appliance_type=appliance_type,
            remote_start_allowed=remote_allowed,
            is_running=is_running,
            status=status,
        )

    async def start_appliance(
        self,
        appliance_type: ApplianceType,
        delay_seconds: int = 0,
    ) -> None:
        """Start appliance via HA power switch.

        For dishwasher, delay_seconds sets start_relative (seconds until start).
        For washer/dryer, delay_seconds sets finish_relative (seconds until end).

        Args:
            appliance_type: Which appliance to start.
            delay_seconds: Delay before/until start, 0 = immediate start.
        """
        cfg = _APPLIANCE_CONFIG[appliance_type]
        if delay_seconds > 0 and "delay_entity" in cfg:
            await self._call_service(
                "number", "set_value", cfg["delay_entity"], value=delay_seconds
            )
        await self._call_service("switch", "turn_on", cfg["power_switch"])
        self._log.info(
            "appliance_start_command_sent",
            appliance=appliance_type.value,
            delay_s=delay_seconds,
        )
