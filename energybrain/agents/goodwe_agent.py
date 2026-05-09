"""GoodWeAgent — reads PV production from GoodWe GW5K-ET inverter.

Night behaviour: GoodWe enters sleep mode after sunset → all realtime sensors
become 'unavailable'. This is normal. Fall back to 0W for power, keep daily total.
AC-coupled battery note: Marstek Venus is AC-coupled — GoodWe does NOT see battery.
"""
from __future__ import annotations

from energybrain.agents.base_agent import BaseAgent
from energybrain.models import DeviceStatus, PVState
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

_PV_POWER_ENTITY = "sensor.goodwe_pv_power"
_TODAY_KWH_ENTITY = "sensor.goodwe_today_s_pv_generation"


class GoodWeAgent(BaseAgent[PVState]):
    """Reads PV power and daily production from the GoodWe inverter."""

    AGENT_NAME = "goodwe_agent"

    async def collect(self) -> PVState:
        # pv_power is 'unavailable' at night — fallback 0W is correct
        pv_power, pv_st = await self._get_float(_PV_POWER_ENTITY, fallback=0.0)
        today_kwh, today_st = await self._get_float(_TODAY_KWH_ENTITY, fallback=0.0)

        # Night sleep (pv offline, daily online) is ONLINE — inverter is healthy
        if pv_st == DeviceStatus.OFFLINE and today_st == DeviceStatus.ONLINE:
            status = DeviceStatus.ONLINE
        else:
            status = self._determine_status(pv_st, today_st)

        self._log.debug(
            "goodwe_collected",
            pv_power_w=pv_power,
            today_kwh=today_kwh,
            status=status.value,
        )
        return PVState(
            power_w=pv_power,
            daily_energy_kwh=today_kwh,
            status=status,
        )
