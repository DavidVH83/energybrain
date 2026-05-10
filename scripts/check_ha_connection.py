"""Check HA connection and verify all required entity IDs are accessible.

Usage:
    python scripts/check_ha_connection.py

Requires a valid .env with HA_URL and HA_TOKEN.
Prints a pass/fail table for every entity EnergyBrain reads.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# Allow running from the project root without installing
sys.path.insert(0, str(Path(__file__).parent.parent))

from energybrain.config import ConfigError, load_config
from energybrain.utils.ha_client import HAClient

# All entity IDs EnergyBrain reads (from spec section 4)
_REQUIRED_ENTITIES = [
    # GoodWe
    "sensor.goodwe_pv_power",
    "sensor.goodwe_on_grid_power",
    "sensor.goodwe_today_generation",
    "sensor.goodwe_operating_mode",
    # Marstek
    "sensor.marstek_venuse_state_of_charge",
    "sensor.marstek_venuse_battery_power",
    "sensor.marstek_venuse_battery_temperature",
    "sensor.marstek_venuse_working_mode",
    # P1 / HomeWizard
    "sensor.homewizard_p1_power",
    "sensor.homewizard_p1_total_power_import_kwh",
    "sensor.homewizard_p1_total_power_export_kwh",
    # Plugwise Anna
    "climate.anna",
    "select.anna_thermostaat_schema",
    "sensor.anna_outdoor_temperature",
    # Home Connect — dishwasher
    "binary_sensor.siemens_dishwasher_remote_start_allowance_state",
    "binary_sensor.siemens_dishwasher_door_state",
    "sensor.siemens_dishwasher_operation_state",
    # Home Connect — washing machine
    "binary_sensor.siemens_washing_machine_remote_start_allowance_state",
    "sensor.siemens_washing_machine_operation_state",
    # Home Connect — dryer
    "binary_sensor.siemens_dryer_remote_start_allowance_state",
    "sensor.siemens_dryer_operation_state",
]


async def _run() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[ERROR] Config: {exc}")
        return 1

    print(f"Connecting to {config.ha_url} ...")
    ha = HAClient(config.ha_url, config.ha_token)
    try:
        await ha.open()
    except Exception as exc:
        print(f"[ERROR] Cannot open HA session: {exc}")
        return 1

    passed = 0
    failed = 0

    print(f"\n{'Entity ID':<60} {'State':<15} {'Status'}")
    print("-" * 90)

    for entity_id in _REQUIRED_ENTITIES:
        try:
            state = await ha.get_state(entity_id)
            val = state.get("state", "?")[:14]
            status = "OK" if val not in ("unavailable", "unknown", "None", "?") else "WARN"
            marker = "✓" if status == "OK" else "⚠"
            print(f"{entity_id:<60} {val:<15} {marker}")
            if status == "OK":
                passed += 1
            else:
                failed += 1
        except Exception as exc:
            print(f"{entity_id:<60} {'ERROR':<15} ✗  ({exc})")
            failed += 1

    await ha.close()

    print("-" * 90)
    print(f"\nResult: {passed} OK, {failed} failed/unavailable out of {len(_REQUIRED_ENTITIES)} entities")
    return 0 if failed == 0 else 2


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
