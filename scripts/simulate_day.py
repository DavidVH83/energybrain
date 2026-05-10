"""Simulate a full EnergyBrain day (06:00-23:00) without real hardware.

Walks through the day in configurable steps, printing what actions
DayPlanner would schedule and when they would fire.

Usage:
    python scripts/simulate_day.py
    python scripts/simulate_day.py --scenario cloudy
    python scripts/simulate_day.py --scenario sunny --step 15
    python scripts/simulate_day.py --scenario cold_winter

Scenarios:
    sunny       — high PV (peak 5500W), mild temperature (18°C)
    cloudy      — low PV (peak 800W), mild temperature (15°C)
    cold_winter — moderate PV (peak 2000W), cold outdoor (-2°C), needs heating
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from energybrain.intelligence.day_planner import DayPlanner
from energybrain.intelligence.oscillation_detector import OscillationDetector
from energybrain.intelligence.pattern_learner import PatternLearner
from energybrain.intelligence.week_strategist import WeekStrategist
from energybrain.models import (
    ApplianceState,
    ApplianceType,
    BatteryMode,
    BatteryState,
    DeviceStatus,
    DayPlan,
    EnergyPrice,
    GridState,
    HVACMode,
    HeatPumpState,
    HourlyForecast,
    PVState,
    SystemState,
    WeatherForecast,
    WeekStrategy,
)

# ---------------------------------------------------------------------------
# Synthetic PV curve helpers
# ---------------------------------------------------------------------------

_PV_CURVE_SUNNY = [
    0, 0, 0, 0, 0, 0, 50, 300, 900, 1800, 3200, 4500,
    5500, 5200, 4800, 3800, 2500, 1200, 400, 100, 0, 0, 0, 0,
]
_PV_CURVE_CLOUDY = [
    0, 0, 0, 0, 0, 0, 20, 100, 300, 600, 750, 800,
    780, 700, 650, 500, 300, 100, 30, 0, 0, 0, 0, 0,
]
_PV_CURVE_COLD_WINTER = [
    0, 0, 0, 0, 0, 0, 0, 50, 300, 900, 1600, 2000,
    1900, 1700, 1400, 800, 300, 50, 0, 0, 0, 0, 0, 0,
]

_SCENARIOS: dict[str, dict] = {
    "sunny": {
        "pv_curve": _PV_CURVE_SUNNY,
        "outdoor_c": 18.0,
        "indoor_c": 20.5,
        "cloud_pct": 10.0,
        "hvac_mode": HVACMode.AUTO,
        "description": "Zonnige dag — hoge PV, gematigde temperatuur",
    },
    "cloudy": {
        "pv_curve": _PV_CURVE_CLOUDY,
        "outdoor_c": 14.0,
        "indoor_c": 19.5,
        "cloud_pct": 85.0,
        "hvac_mode": HVACMode.AUTO,
        "description": "Bewolkte dag — lage PV, alleen deadlines tellen",
    },
    "cold_winter": {
        "pv_curve": _PV_CURVE_COLD_WINTER,
        "outdoor_c": -2.0,
        "indoor_c": 18.0,
        "cloud_pct": 40.0,
        "hvac_mode": HVACMode.HEAT,
        "description": "Koude winterdag — vorst buiten, verwarming nodig",
    },
}


def _build_state(scenario: dict, hour: int, soc_pct: float = 50.0) -> SystemState:
    pv_w = float(scenario["pv_curve"][hour])
    grid_w = -(pv_w * 0.3)  # simulate partial self-consumption
    hourly = [
        HourlyForecast(
            hour=h,
            pv_estimated_w=float(scenario["pv_curve"][h]),
            cloud_cover_pct=scenario["cloud_pct"],
            temperature_c=scenario["outdoor_c"],
        )
        for h in range(24)
    ]
    daily_pv = sum(scenario["pv_curve"]) / 1000.0
    return SystemState(
        pv=PVState(power_w=pv_w, daily_energy_kwh=daily_pv * (hour / 24)),
        battery=BatteryState(soc_pct=soc_pct, power_w=0.0, temperature_c=25.0,
                             mode=BatteryMode.AUTO),
        grid=GridState(power_w=grid_w, daily_import_kwh=1.0, daily_export_kwh=2.0),
        heat_pump=HeatPumpState(
            indoor_temp_c=scenario["indoor_c"],
            outdoor_temp_c=scenario["outdoor_c"],
            setpoint_c=20.0,
            hvac_mode=scenario["hvac_mode"],
            dhw_boost_active=False,
            dhw_temp_c=50.0,
        ),
        appliances={
            ApplianceType.DISHWASHER: ApplianceState(
                appliance_type=ApplianceType.DISHWASHER,
                remote_start_allowed=True,
                is_running=False,
                status=DeviceStatus.ONLINE,
            ),
            ApplianceType.WASHING_MACHINE: ApplianceState(
                appliance_type=ApplianceType.WASHING_MACHINE,
                remote_start_allowed=True,
                is_running=False,
                status=DeviceStatus.ONLINE,
            ),
            ApplianceType.DRYER: ApplianceState(
                appliance_type=ApplianceType.DRYER,
                remote_start_allowed=True,
                is_running=False,
                status=DeviceStatus.ONLINE,
            ),
        },
        weather=WeatherForecast(
            location="Korbeek-lo (simulated)",
            daily_pv_kwh=daily_pv,
            hourly=hourly,
        ),
        prices=EnergyPrice(
            current_import_eur_kwh=0.25,
            current_export_eur_kwh=0.036,
            hourly_import_prices=[0.25] * 24,
            cheap_hours=[1, 2, 3, 4, 5],
            expensive_hours=[17, 18, 19, 20],
        ),
    )


def _bar(w: float, max_w: float = 6000.0, width: int = 20) -> str:
    filled = int(min(w / max_w, 1.0) * width)
    return "█" * filled + "░" * (width - filled)


def _run(scenario_name: str, step_min: int) -> None:
    scenario = _SCENARIOS[scenario_name]
    print(f"\nEnergyBrain — Dag Simulatie")
    print(f"Scenario: {scenario_name} — {scenario['description']}")
    print(f"Stap: {step_min} minuten\n")

    # Build initial state at 06:00
    state = _build_state(scenario, hour=6)
    planner = DayPlanner()
    day_plan: Optional[DayPlan] = planner.create_day_plan(state)

    if day_plan:
        notif = planner.build_morning_notification(day_plan, state)
        if notif:
            print(f"📱 Ochtendnotificatie:\n   {notif}\n")
        best_w = max(day_plan.surplus_windows, key=lambda w: w.avg_surplus_w) \
            if day_plan.surplus_windows else None
        if best_w:
            print(f"Gepland overschotvenster: "
                  f"{best_w.start_hour}:00 – {best_w.end_hour}:00  "
                  f"({best_w.avg_surplus_w:.0f}W gem)")
        else:
            print("Geen overschotvenster gedetecteerd")
        if day_plan.scheduled_tasks:
            print(f"Geplande taken:")
            for t in day_plan.scheduled_tasks:
                start = t.planned_start.strftime("%H:%M")
                print(f"  • {t.name:<25} start={start}  prioriteit={t.priority}")
        else:
            print("Geen taken gepland (onvoldoende surplus of geen toestellen geladen)")
    else:
        print("Geen dagplan gemaakt.")

    # Walk through the day
    print(f"\n{'Uur':<6} {'PV (W)':<10} {'PV bar':<22} {'SoC':<6} {'Surplus'}")
    print("-" * 65)

    soc = 50.0
    sim_date = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
    end_time = sim_date.replace(hour=23, minute=0)
    step = timedelta(minutes=step_min)
    current = sim_date

    while current <= end_time:
        h = current.hour
        pv_w = float(scenario["pv_curve"][h])
        surplus_w = max(0.0, pv_w - 1200.0)  # assume 1200W base load

        # Naive SoC simulation
        if surplus_w > 500:
            soc = min(95.0, soc + surplus_w / 5120 * (step_min / 60) * 100)
        elif pv_w < 200:
            soc = max(10.0, soc - 0.5 * (step_min / 60))

        print(f"{current.strftime('%H:%M'):<6} {pv_w:<10.0f} {_bar(pv_w):<22} "
              f"{soc:4.0f}%  {surplus_w:.0f}W")
        current += step

    # Week strategy glimpse
    print(f"\n{'=' * 65}")
    week_strategist = WeekStrategist()
    from unittest.mock import MagicMock
    mock_thermal = MagicMock()
    mock_thermal.is_ready.return_value = False
    mock_od = MagicMock()
    mock_od.is_frozen.return_value = False
    week_forecast = [
        {"day_index": i, "avg_outdoor_c": scenario["outdoor_c"],
         "min_outdoor_c": scenario["outdoor_c"] - 3.0,
         "daily_pv_kwh": sum(scenario["pv_curve"]) / 1000.0}
        for i in range(7)
    ]
    ws = week_strategist.calculate_strategy(mock_thermal, None, mock_od, week_forecast)
    explanation = week_strategist.explain_strategy(ws)
    print(f"\nWeekstrategie (als vandaag elke dag was):")
    for line in explanation.strip().split("\n"):
        print(f"  {line}")


def main() -> None:
    parser = argparse.ArgumentParser(description="EnergyBrain dag simulatie")
    parser.add_argument("--scenario", choices=list(_SCENARIOS), default="sunny",
                        help="Dag scenario (default: sunny)")
    parser.add_argument("--step", type=int, default=60, metavar="MIN",
                        help="Tijdstap in minuten (default: 60)")
    args = parser.parse_args()
    _run(args.scenario, args.step)


if __name__ == "__main__":
    main()
