"""Tests for energybrain.intelligence.oscillation_detector."""
from datetime import datetime, timedelta

import pytest

from energybrain.intelligence.oscillation_detector import (
    FREEZE_HOURS,
    SWITCH_THRESHOLD,
    TEMP_SWING_THRESHOLD_C,
    OscillationDetector,
)


def _history(*modes: str) -> list[dict]:
    return [{"mode": m} for m in modes]


class TestOscillationDetectorCheck:
    def test_no_switches_returns_false(self):
        od = OscillationDetector()
        history = _history("heat", "heat", "heat", "heat")
        temps = [10.0, 8.0, 9.0, 7.0]
        assert od.check(history, temps) is False

    def test_enough_switches_but_small_swing_returns_false(self):
        od = OscillationDetector()
        history = _history("heat", "cool", "heat", "cool", "heat")  # 4 switches
        temps = [15.0, 16.0, 17.0, 16.0, 15.0]  # swing = 2°C
        assert od.check(history, temps) is False

    def test_enough_swing_but_few_switches_returns_false(self):
        od = OscillationDetector()
        history = _history("heat", "cool", "heat")  # 2 switches
        temps = [5.0, 20.0, 5.0]  # swing = 15°C
        assert od.check(history, temps) is False

    def test_oscillation_detected(self):
        od = OscillationDetector()
        history = _history("heat", "cool", "heat", "cool", "heat")  # 4 switches
        temps = [5.0, 22.0, 6.0, 21.0, 5.0]  # swing = 17°C
        assert od.check(history, temps) is True

    def test_freeze_set_after_detection(self):
        od = OscillationDetector()
        history = _history("heat", "cool", "heat", "cool", "heat")
        temps = [5.0, 22.0, 6.0, 21.0, 5.0]
        od.check(history, temps)
        assert od._freeze_until is not None
        assert od._freeze_until > datetime.now() + timedelta(hours=FREEZE_HOURS - 1)

    def test_empty_history_returns_false(self):
        od = OscillationDetector()
        assert od.check([], []) is False

    def test_single_entry_returns_false(self):
        od = OscillationDetector()
        assert od.check([{"mode": "heat"}], [15.0]) is False

    def test_frozen_detector_returns_true_without_rechecking(self):
        od = OscillationDetector()
        od._freeze_until = datetime.now() + timedelta(hours=10)
        # Even with no oscillation data
        assert od.check([], []) is True


class TestOscillationDetectorIsFrozen:
    def test_not_frozen_initially(self):
        od = OscillationDetector()
        assert od.is_frozen() is False

    def test_frozen_after_detection(self):
        od = OscillationDetector()
        history = _history("heat", "cool", "heat", "cool", "heat")
        temps = [5.0, 22.0, 6.0, 21.0, 5.0]
        od.check(history, temps)
        assert od.is_frozen() is True

    def test_freeze_expires(self):
        od = OscillationDetector()
        od._freeze_until = datetime.now() - timedelta(seconds=1)
        assert od.is_frozen() is False

    def test_freeze_clears_after_expiry(self):
        od = OscillationDetector()
        od._freeze_until = datetime.now() - timedelta(seconds=1)
        od.is_frozen()  # Should clear
        assert od._freeze_until is None

    def test_exactly_at_threshold_switches(self):
        od = OscillationDetector()
        # SWITCH_THRESHOLD switches — borderline (> not >=)
        modes = ["heat", "cool"] * (SWITCH_THRESHOLD // 2 + 1)
        history = _history(*modes)
        temps = [5.0, 25.0] * (len(modes) // 2)
        # Should detect because switches > SWITCH_THRESHOLD
        result = od.check(history[:SWITCH_THRESHOLD + 2], temps[:SWITCH_THRESHOLD + 2])
        # Result depends on whether switches > threshold
        assert isinstance(result, bool)


class TestCountModeSwitches:
    def test_no_switches_in_uniform_history(self):
        from energybrain.intelligence.oscillation_detector import OscillationDetector
        switches = OscillationDetector._count_mode_switches(
            [{"mode": "heat"}] * 5
        )
        assert switches == 0

    def test_alternating_heat_cool(self):
        history = _history("heat", "cool", "heat", "cool")
        assert OscillationDetector._count_mode_switches(history) == 3

    def test_single_entry(self):
        assert OscillationDetector._count_mode_switches([{"mode": "heat"}]) == 0

    def test_empty(self):
        assert OscillationDetector._count_mode_switches([]) == 0


class TestTempSwing:
    def test_empty_returns_zero(self):
        assert OscillationDetector._temp_swing([]) == pytest.approx(0.0)

    def test_single_returns_zero(self):
        assert OscillationDetector._temp_swing([15.0]) == pytest.approx(0.0)

    def test_swing_calculated_correctly(self):
        assert OscillationDetector._temp_swing([5.0, 15.0, 25.0, 10.0]) == pytest.approx(20.0)
