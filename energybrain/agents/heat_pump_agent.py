"""HeatPumpAgent — reads state from Plugwise Anna OpenTherm heat pump.

Also provides manual override detection:
  If |current_setpoint - last_set_setpoint| > 0.5°C AND brain didn't change it
  → user manually adjusted Anna → respect the change immediately.

Anna schedule (select.anna_thermostaat_schema) must remain 'off'. Checked at
startup by StartupRecovery and every 15 min by the orchestrator.
"""
from __future__ import annotations

from energybrain.agents.base_agent import BaseAgent
from energybrain.models import DeviceStatus, HVACMode, HeatPumpState
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

_INDOOR_TEMP_ENTITY = "sensor.anna_temperatuur"
_OUTDOOR_TEMP_ENTITY = "sensor.smile_anna_buitentemperatuur"
_SETPOINT_ENTITY = "sensor.anna_instelpunt"
_CLIMATE_ENTITY = "climate.anna"
_DHW_TEMP_ENTITY = "sensor.opentherm_sww_temperatuur"
_SSW_MODUS_ENTITY = "select.opentherm_ssw_modus"

# Minimum setpoint delta to call a change a manual override
OVERRIDE_DETECTION_DELTA_C = 0.5


def _parse_hvac_mode(raw: str) -> HVACMode:
    mapping = {
        "heat": HVACMode.HEAT,
        "cool": HVACMode.COOL,
        "off": HVACMode.OFF,
        "auto": HVACMode.AUTO,
    }
    return mapping.get(raw.lower().strip(), HVACMode.AUTO)


class HeatPumpAgent(BaseAgent[HeatPumpState]):
    """Reads current heat pump state and exposes manual override detection."""

    AGENT_NAME = "heat_pump_agent"

    async def collect(self) -> HeatPumpState:
        indoor_temp_c, indoor_st = await self._get_float(_INDOOR_TEMP_ENTITY, fallback=20.0)
        outdoor_temp_c, outdoor_st = await self._get_float(_OUTDOOR_TEMP_ENTITY, fallback=10.0)
        setpoint_c, setpoint_st = await self._get_float(_SETPOINT_ENTITY, fallback=20.0)
        hvac_mode_str, _ = await self._get_str(_CLIMATE_ENTITY, fallback="heat")
        dhw_temp_c, dhw_st = await self._get_float(_DHW_TEMP_ENTITY, fallback=0.0)
        ssw_modus, _ = await self._get_str(_SSW_MODUS_ENTITY, fallback="auto")

        hvac_mode = _parse_hvac_mode(hvac_mode_str)
        dhw_boost_active = ssw_modus.lower() == "boost"
        dhw_temp: float | None = dhw_temp_c if dhw_st == DeviceStatus.ONLINE else None
        status = self._determine_status(indoor_st, outdoor_st, setpoint_st)

        self._log.debug(
            "heat_pump_collected",
            indoor_c=indoor_temp_c,
            outdoor_c=outdoor_temp_c,
            setpoint_c=setpoint_c,
            hvac_mode=hvac_mode.value,
            dhw_boost=dhw_boost_active,
            status=status.value,
        )
        return HeatPumpState(
            indoor_temp_c=indoor_temp_c,
            outdoor_temp_c=outdoor_temp_c,
            setpoint_c=setpoint_c,
            hvac_mode=hvac_mode,
            dhw_boost_active=dhw_boost_active,
            dhw_temp_c=dhw_temp,
            status=status,
        )

    def detect_manual_override(
        self,
        current_setpoint: float,
        last_set_setpoint: float,
        brain_changed_this_cycle: bool = False,
    ) -> bool:
        """True if the user manually changed the Anna setpoint this cycle.

        Args:
            current_setpoint: Setpoint read from HA right now.
            last_set_setpoint: Setpoint EnergyBrain last wrote.
            brain_changed_this_cycle: True if brain just issued a setpoint change.
        """
        if brain_changed_this_cycle:
            return False
        return abs(current_setpoint - last_set_setpoint) > OVERRIDE_DETECTION_DELTA_C
