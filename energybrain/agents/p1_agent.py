"""P1Agent — reads grid power and energy totals from HomeWizard P1 meter.

Primary source for GridState. Belgian smart meter (Fluvius DSMR v5.0).
Positive power = consuming from grid. Negative = injecting surplus.
"""
from __future__ import annotations

from energybrain.agents.base_agent import BaseAgent
from energybrain.models import DeviceStatus, GridState
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

_POWER_ENTITY = "sensor.p1_meter_vermogen"
_IMPORT_T1_ENTITY = "sensor.p1_meter_energie_import_tarief_1"
_IMPORT_T2_ENTITY = "sensor.p1_meter_energie_import_tarief_2"
_EXPORT_T1_ENTITY = "sensor.p1_meter_energie_export_tarief_1"
_EXPORT_T2_ENTITY = "sensor.p1_meter_energie_export_tarief_2"


class P1Agent(BaseAgent[GridState]):
    """Reads current grid power and cumulative energy from the P1 smart meter."""

    AGENT_NAME = "p1_agent"

    async def collect(self) -> GridState:
        power_w, power_st = await self._get_float(_POWER_ENTITY, fallback=0.0)
        import_t1, import_st1 = await self._get_float(_IMPORT_T1_ENTITY, fallback=0.0)
        import_t2, import_st2 = await self._get_float(_IMPORT_T2_ENTITY, fallback=0.0)
        export_t1, export_st1 = await self._get_float(_EXPORT_T1_ENTITY, fallback=0.0)
        export_t2, _ = await self._get_float(_EXPORT_T2_ENTITY, fallback=0.0)

        status = self._determine_status(power_st, import_st1, export_st1)

        self._log.debug(
            "p1_collected",
            power_w=power_w,
            import_kwh=import_t1 + import_t2,
            export_kwh=export_t1 + export_t2,
            status=status.value,
        )
        return GridState(
            power_w=power_w,
            daily_import_kwh=import_t1 + import_t2,
            daily_export_kwh=export_t1 + export_t2,
            status=status,
        )
