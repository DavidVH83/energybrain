"""MarstekAgent — reads battery state from Marstek Venus E 5.12 kWh.

All write methods are STUBS while MARSTEK_WRITE_ENABLED=false.
V153 firmware has a known RS485/UDP write regression. Fix expected in V154.
CT clamp currently disconnected → battery.status=ERROR when ct_connected=off.
"""
from __future__ import annotations

from energybrain.agents.base_agent import BaseAgent
from energybrain.config import Config
from energybrain.exceptions import StubActionError
from energybrain.models import BatteryMode, BatteryState, DeviceStatus
from energybrain.utils.ha_client import HAClient
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

_SOC_ENTITY = "sensor.marstek_venuse_state_of_charge"
_POWER_ENTITY = "sensor.marstek_venuse_power"
_TEMP_ENTITY = "sensor.marstek_venuse_battery_temperature"
_MODE_ENTITY = "select.marstek_venuse_operating_mode"
_CT_CONNECTED_ENTITY = "binary_sensor.marstek_venuse_ct_connected"


def _parse_battery_mode(raw: str) -> BatteryMode:
    mapping = {
        "auto": BatteryMode.AUTO,
        "ai": BatteryMode.AI,
        "manual": BatteryMode.MANUAL,
        "passive": BatteryMode.PASSIVE,
    }
    return mapping.get(raw.lower().strip(), BatteryMode.AUTO)


class MarstekAgent(BaseAgent[BatteryState]):
    """Reads battery state. All writes are stubs until V154 firmware."""

    AGENT_NAME = "marstek_agent"

    def __init__(self, ha: HAClient, config: Config) -> None:
        super().__init__(ha)
        self._config = config

    async def collect(self) -> BatteryState:
        soc_pct, soc_st = await self._get_float(_SOC_ENTITY, fallback=0.0)
        power_w, pwr_st = await self._get_float(_POWER_ENTITY, fallback=0.0)
        temperature_c, _ = await self._get_float(_TEMP_ENTITY, fallback=25.0)
        mode_str, _ = await self._get_str(_MODE_ENTITY, fallback="Auto")
        ct_connected, ct_st = await self._get_bool(_CT_CONNECTED_ENTITY, fallback=True)

        # CT not connected via local API — happens with P1 Beta mode (V153 firmware).
        # Marstek IS working (app shows live data) but local API doesn't expose
        # measurement registers in this configuration.
        ct_no_local = ct_st == DeviceStatus.ONLINE and not ct_connected

        if ct_no_local:
            status = DeviceStatus.ERROR
            # SoC sensor is also unavailable in P1 Beta mode — use 50% as neutral
            # assumption instead of 0% to avoid false "battery empty" decisions.
            if soc_st != DeviceStatus.ONLINE:
                soc_pct = 50.0
                self._log.warning(
                    "marstek_soc_unavailable",
                    reason="P1 Beta mode: local API does not expose SoC. Using 50% neutral fallback.",
                    firmware="V153",
                )
        else:
            status = self._determine_status(soc_st, pwr_st)

        mode = _parse_battery_mode(mode_str)

        self._log.debug(
            "marstek_collected",
            soc_pct=soc_pct,
            power_w=power_w,
            mode=mode.value,
            ct_connected=ct_connected,
            status=status.value,
        )
        return BatteryState(
            soc_pct=soc_pct,
            power_w=power_w,
            temperature_c=temperature_c,
            mode=mode,
            write_enabled=self._config.marstek_write_enabled,
            status=status,
        )

    async def set_mode(self, mode: BatteryMode) -> None:
        """Set battery operating mode. STUB until Marstek V154 firmware.

        Raises:
            StubActionError: Always, while MARSTEK_WRITE_ENABLED=false.
        """
        if not self._config.marstek_write_enabled:
            self._log.info(
                "marstek_write_stub",
                intent="set_mode",
                mode=mode.value,
                reason="MARSTEK_WRITE_ENABLED=false — V153 firmware write bug",
            )
            raise StubActionError(
                f"Marstek write disabled. Would set operating mode to {mode.value!r}"
            )
        await self._call_service(
            "select",
            "select_option",
            _MODE_ENTITY,
            option=mode.value,
        )
