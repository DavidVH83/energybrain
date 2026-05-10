"""OscillationDetector — prevents the heating/cooling energy-wasting oscillation.

Detection: count HVAC mode switches in last 7 days.
If > SWITCH_THRESHOLD AND outdoor temp swing > TEMP_SWING_THRESHOLD_C:
    freeze strategy for FREEZE_HOURS.

During freeze:
    - No HVAC mode changes allowed (except hard limits)
    - WeekStrategist enters neutral mode
    - User notified
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

SWITCH_THRESHOLD = 3
TEMP_SWING_THRESHOLD_C = 8.0
FREEZE_HOURS = 48


class OscillationDetector:
    """Detects heating-cooling oscillation and freezes HVAC strategy to prevent it."""

    def __init__(self) -> None:
        self._freeze_until: Optional[datetime] = None
        self._log = get_logger("oscillation_detector")

    def check(self, hvac_history: list[dict], outdoor_temps: list[float]) -> bool:
        """Return True if oscillation pattern detected and freeze triggered.

        Args:
            hvac_history: List of dicts with keys 'mode' (str) and 'timestamp' (datetime).
                          Should cover last 7 days.
            outdoor_temps: List of outdoor temperatures (°C) over the same period.

        Returns:
            True if oscillation detected (freeze started or already active).
        """
        if self.is_frozen():
            return True

        switches = self._count_mode_switches(hvac_history)
        temp_swing = self._temp_swing(outdoor_temps)

        if switches > SWITCH_THRESHOLD and temp_swing > TEMP_SWING_THRESHOLD_C:
            self._freeze_until = datetime.now() + timedelta(hours=FREEZE_HOURS)
            self._log.warning(
                "oscillation_detected",
                mode_switches=switches,
                temp_swing_c=round(temp_swing, 1),
                freeze_until=self._freeze_until.isoformat(),
            )
            return True
        return False

    def is_frozen(self) -> bool:
        """True if strategy changes are currently blocked."""
        if self._freeze_until is None:
            return False
        if datetime.now() < self._freeze_until:
            return True
        self._freeze_until = None
        return False

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _count_mode_switches(hvac_history: list[dict]) -> int:
        """Count HVAC mode transitions in the history list."""
        if len(hvac_history) < 2:
            return 0
        switches = 0
        prev = hvac_history[0].get("mode", "")
        for entry in hvac_history[1:]:
            current = entry.get("mode", "")
            if current != prev:
                switches += 1
            prev = current
        return switches

    @staticmethod
    def _temp_swing(outdoor_temps: list[float]) -> float:
        """Return max - min outdoor temperature over the period."""
        if not outdoor_temps:
            return 0.0
        return max(outdoor_temps) - min(outdoor_temps)
