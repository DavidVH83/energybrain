"""Run all EnergyBrain agents and print the current SystemState.

Usage:
    python scripts/check_all_agents.py

Requires a valid .env with HA credentials. Prints a structured summary
of everything EnergyBrain reads: PV, battery, grid, heat pump, appliances,
weather, and prices.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from energybrain.agents.energy_price_agent import EnergyPriceAgent
from energybrain.agents.goodwe_agent import GoodWeAgent
from energybrain.agents.heat_pump_agent import HeatPumpAgent
from energybrain.agents.home_connect_agent import HomeConnectAgent
from energybrain.agents.marstek_agent import MarstekAgent
from energybrain.agents.p1_agent import P1Agent
from energybrain.agents.weather_agent import WeatherAgent
from energybrain.config import ConfigError, load_config
from energybrain.utils.ha_client import HAClient


def _section(title: str) -> None:
    print(f"\n{'=' * 50}")
    print(f"  {title}")
    print("=" * 50)


async def _run() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"[ERROR] Config: {exc}")
        return 1

    print(f"EnergyBrain — agent check at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"HA: {config.ha_url}")

    ha = HAClient(config.ha_url, config.ha_token)
    errors: list[str] = []

    try:
        await ha.open()
    except Exception as exc:
        print(f"[ERROR] Cannot connect to HA: {exc}")
        return 1

    # GoodWe
    _section("GoodWe PV")
    try:
        pv = await GoodWeAgent(ha).get_pv_state()
        print(f"  PV power:        {pv.power_w:.0f} W")
        print(f"  Daily energy:    {pv.daily_energy_kwh:.2f} kWh")
        print(f"  Status:          {pv.status.value}")
    except Exception as exc:
        print(f"  [ERROR] {exc}")
        errors.append(f"GoodWeAgent: {exc}")

    # Marstek
    _section("Marstek Battery")
    try:
        bat = await MarstekAgent(ha, config).get_battery_state()
        print(f"  SoC:             {bat.soc_pct:.1f}%")
        print(f"  Power:           {bat.power_w:.0f} W")
        print(f"  Temperature:     {bat.temperature_c:.1f}°C")
        print(f"  Mode:            {bat.mode.value}")
        print(f"  Write enabled:   {bat.write_enabled}")
    except Exception as exc:
        print(f"  [ERROR] {exc}")
        errors.append(f"MarstekAgent: {exc}")

    # P1
    _section("P1 / HomeWizard Grid")
    try:
        grid = await P1Agent(ha).get_grid_state()
        print(f"  Grid power:      {grid.power_w:.0f} W  ({'import' if grid.power_w > 0 else 'export'})")
        print(f"  Daily import:    {grid.daily_import_kwh:.2f} kWh")
        print(f"  Daily export:    {grid.daily_export_kwh:.2f} kWh")
        print(f"  Surplus:         {grid.surplus_w:.0f} W")
    except Exception as exc:
        print(f"  [ERROR] {exc}")
        errors.append(f"P1Agent: {exc}")

    # Heat pump
    _section("Plugwise Anna Heat Pump")
    try:
        hp = await HeatPumpAgent(ha).get_heat_pump_state()
        print(f"  Indoor temp:     {hp.indoor_temp_c:.1f}°C")
        print(f"  Outdoor temp:    {hp.outdoor_temp_c:.1f}°C")
        print(f"  Setpoint:        {hp.setpoint_c:.1f}°C")
        print(f"  HVAC mode:       {hp.hvac_mode.value}")
        print(f"  DHW boost:       {hp.dhw_boost_active}")
        print(f"  DHW temp:        {hp.dhw_temp_c:.1f}°C")
    except Exception as exc:
        print(f"  [ERROR] {exc}")
        errors.append(f"HeatPumpAgent: {exc}")

    # Home Connect
    _section("Home Connect Appliances")
    try:
        appliances = await HomeConnectAgent(ha).get_appliance_states()
        for atype, astate in appliances.items():
            remote = "remote_ok" if astate.remote_start_allowed else "no_remote"
            running = "RUNNING" if astate.is_running else "idle"
            print(f"  {atype.value:<20} {running:<10} {remote}  status={astate.status.value}")
    except Exception as exc:
        print(f"  [ERROR] {exc}")
        errors.append(f"HomeConnectAgent: {exc}")

    # Weather
    _section("Weather")
    try:
        forecast = await WeatherAgent(config).get_forecast()
        print(f"  Location:        {forecast.location}")
        print(f"  Daily PV:        {forecast.daily_pv_kwh:.1f} kWh")
        now_h = datetime.now().hour
        current = next((h for h in forecast.hourly if h.hour == now_h), None)
        if current:
            print(f"  Current hour ({now_h:02d}h): {current.pv_estimated_w:.0f}W PV, "
                  f"{current.cloud_cover_pct:.0f}% cloud, {current.temperature_c:.1f}°C")
    except Exception as exc:
        print(f"  [ERROR] {exc}")
        errors.append(f"WeatherAgent: {exc}")

    # Prices
    _section("Energy Prices")
    try:
        prices = await EnergyPriceAgent(config).get_prices()
        print(f"  Import (now):    €{prices.current_import_eur_kwh:.4f}/kWh")
        print(f"  Export (now):    €{prices.current_export_eur_kwh:.4f}/kWh")
        print(f"  Cheap hours:     {prices.cheap_hours[:5]}{'...' if len(prices.cheap_hours) > 5 else ''}")
        print(f"  Expensive hours: {prices.expensive_hours[:5]}{'...' if len(prices.expensive_hours) > 5 else ''}")
    except Exception as exc:
        print(f"  [ERROR] {exc}")
        errors.append(f"EnergyPriceAgent: {exc}")

    await ha.close()

    print(f"\n{'=' * 50}")
    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  ✗ {e}")
        return 1
    else:
        print("All agents OK")
        return 0


def main() -> None:
    sys.exit(asyncio.run(_run()))


if __name__ == "__main__":
    main()
