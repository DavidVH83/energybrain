"""WeekStrategist — 7-day thermal strategy, recalculated nightly at 02:30.

COOLING_ENABLED = False (pending Thermastage feature activation, ~€300 via Thercon Belgium).
Anna cooling mode cannot be toggled via HA — physical button only.
WeekStrategist plans heating/preloading and neutral only.

If OscillationDetector.is_frozen(): return neutral strategy.
Uses ThermalModel.predict_temperature() to simulate scenarios before deciding.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from energybrain.models import WeekStrategy
from energybrain.utils.logging_config import get_logger

if TYPE_CHECKING:
    from energybrain.intelligence.oscillation_detector import OscillationDetector
    from energybrain.intelligence.pv_forecaster import PVForecaster
    from energybrain.intelligence.thermal_model import ThermalModel

logger = get_logger(__name__)

COOLING_ENABLED = False
COOLING_MIN_DAYS = 3
COLD_THRESHOLD_C = 5.0
LOOKFORWARD_DAYS = 5
HEATING_WORTHWHILE_SURPLUS_KWH = 3.0  # Min daily PV to justify preloading


class WeekStrategist:
    """Calculates 7-day thermal strategy for heating / neutral decisions."""

    def __init__(self) -> None:
        self._log = get_logger("week_strategist")

    def calculate_strategy(
        self,
        thermal_model: "ThermalModel",
        forecaster: "PVForecaster",
        oscillation_detector: "OscillationDetector",
        weather_forecast: list[dict] | None = None,
    ) -> WeekStrategy:
        """Full 7-day strategy. Returns neutral if oscillation freeze active.

        Args:
            thermal_model: For temperature simulation.
            forecaster: For daily PV estimates.
            oscillation_detector: Checked first; returns neutral if frozen.
            weather_forecast: List of 7 dicts with keys:
                day_index (0=today), avg_outdoor_c, min_outdoor_c, daily_pv_kwh.
                If None, returns neutral strategy with reasoning note.
        """
        if oscillation_detector.is_frozen():
            self._log.info("week_strategy_neutral_oscillation_frozen")
            return WeekStrategy(
                heating_days=[],
                cooling_days=[],
                neutral_days=list(range(7)),
                oscillation_risk=True,
                reasoning="Oscillatiedetectie actief — geen HVAC-ingrepen voor 48u.",
            )

        if not weather_forecast:
            return WeekStrategy(
                heating_days=[],
                cooling_days=[],
                neutral_days=list(range(7)),
                reasoning="Geen weerspreiding beschikbaar — neutrale strategie.",
            )

        heating_days: list[int] = []
        cooling_days: list[int] = []
        neutral_days: list[int] = []
        reasoning_parts: list[str] = []

        for day_data in weather_forecast[:7]:
            day_idx = day_data.get("day_index", 0)
            avg_outdoor = float(day_data.get("avg_outdoor_c", 10.0))
            min_outdoor = float(day_data.get("min_outdoor_c", 5.0))
            daily_pv_kwh = float(day_data.get("daily_pv_kwh", 0.0))

            decision, note = self._decide_day(
                day_idx, avg_outdoor, min_outdoor, daily_pv_kwh, thermal_model
            )
            if decision == "heat":
                heating_days.append(day_idx)
            elif decision == "cool":
                cooling_days.append(day_idx)
            else:
                neutral_days.append(day_idx)
            if note:
                reasoning_parts.append(f"Dag {day_idx}: {note}")

        strategy = WeekStrategy(
            heating_days=heating_days,
            cooling_days=cooling_days,
            neutral_days=neutral_days,
            oscillation_risk=False,
            reasoning=" | ".join(reasoning_parts) if reasoning_parts else "Geen actie nodig.",
        )
        self._log.info(
            "week_strategy_calculated",
            heating=heating_days,
            cooling=cooling_days,
            neutral=neutral_days,
        )
        return strategy

    def explain_strategy(self, strategy: WeekStrategy) -> str:
        """Human-readable explanation for Monday morning notification."""
        day_names = ["Ma", "Di", "Wo", "Do", "Vr", "Za", "Zo"]
        lines = ["Weekstrategie:"]

        all_days = sorted(
            [(d, "heat") for d in strategy.heating_days]
            + [(d, "cool") for d in strategy.cooling_days]
            + [(d, "neutral") for d in strategy.neutral_days]
        )
        for day_idx, mode in all_days:
            name = day_names[day_idx % 7]
            if mode == "heat":
                lines.append(f"  {name}: Koud verwacht, VVW voorladen op zonnestroom")
            elif mode == "cool":
                lines.append(f"  {name}: Warm, koelingsstrategie actief")
            else:
                lines.append(f"  {name}: Stabiel, geen actie nodig")

        if strategy.oscillation_risk:
            lines.append("⚠️ Oscillatiedetectie — thermische ingrepen gepauzeerd.")
        if strategy.reasoning:
            lines.append(f"({strategy.reasoning})")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _decide_day(
        self,
        day_idx: int,
        avg_outdoor_c: float,
        min_outdoor_c: float,
        daily_pv_kwh: float,
        thermal_model: "ThermalModel",
    ) -> tuple[str, str]:
        """Return (decision, note) for a single day.

        Heating worthwhile if:
        - Cold period expected (min_outdoor < COLD_THRESHOLD_C)
        - Enough PV surplus to make preloading economical
        - ThermalModel confirms house won't recover naturally

        Cooling always disabled (COOLING_ENABLED = False).
        """
        # Cooling disabled
        if avg_outdoor_c > 22.0 and not COOLING_ENABLED:
            return "neutral", "Warm, koeling niet actief (Thermastage niet geactiveerd)"

        # Heating evaluation
        if min_outdoor_c < COLD_THRESHOLD_C:
            if daily_pv_kwh >= HEATING_WORTHWHILE_SURPLUS_KWH:
                # Simulate: will house cool too much without HVAC?
                if self._house_needs_heating(avg_outdoor_c, thermal_model):
                    return "heat", f"Koud ({min_outdoor_c:.1f}°C), VVW voorladen ({daily_pv_kwh:.1f}kWh zon)"
            else:
                return "heat", f"Koud ({min_outdoor_c:.1f}°C), weinig zon maar verwarming nodig"

        return "neutral", ""

    @staticmethod
    def _house_needs_heating(
        avg_outdoor_c: float, thermal_model: "ThermalModel"
    ) -> bool:
        """True if ThermalModel predicts house drops below comfortable range without HVAC."""
        if not thermal_model.is_ready():
            # Conservative fallback: heat if outdoor < 10°C
            return avg_outdoor_c < 10.0

        # Simulate 8 hours without HVAC: 8 identical outdoor temps, no solar, no hvac
        outdoor_8h = [avg_outdoor_c] * 8
        solar_8h = [0.0] * 8
        wind_8h = [3.0] * 8
        hvac_off = [False] * 8

        # Use a typical indoor start of 19°C
        predicted = thermal_model.predict_temperature(
            current_indoor_c=19.0,
            outdoor_forecast=outdoor_8h,
            solar_forecast=solar_8h,
            wind_forecast=wind_8h,
            hvac_plan=hvac_off,
        )
        if not predicted:
            return avg_outdoor_c < 10.0
        return min(predicted) < 17.5  # Below comfortable floor
