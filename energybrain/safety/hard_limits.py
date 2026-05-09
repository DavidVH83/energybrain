"""Hard safety limits — never overridable, checked before every action.

Priority rules (see spec section 5.1 and 14.2):
  Rule 1 (HIGHEST): indoor_temp < indoor_temp_min_winter_c → MUST allow force_heat
  Rule 2:           outdoor_temp < frost_threshold AND indoor_temp >= min → block interference
  Conflict: Rule 1 wins — resident safety beats pump protection.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time
from typing import TYPE_CHECKING, Optional

from energybrain.config import Config
from energybrain.exceptions import SafetyLimitError
from energybrain.models import Action, ActionType, ApplianceType, SystemState
from energybrain.utils.logging_config import get_logger

if TYPE_CHECKING:
    from energybrain.persistence.database import DatabaseManager

logger = get_logger(__name__)

# Setpoint above this is always rejected — floor heating overheat protection.
_ABSOLUTE_MAX_SETPOINT_C = 23.0
# Setpoint below this is always rejected — frost protection.
_ABSOLUTE_MIN_SETPOINT_C = 15.0

# Actions that are purely informational — never blocked by hard limits.
_SAFE_ACTION_TYPES = frozenset({
    ActionType.NO_ACTION,
    ActionType.SEND_NOTIFICATION,
})

# Actions that write to the HVAC — need setpoint / step validation.
_HVAC_ACTION_TYPES = frozenset({
    ActionType.SET_HVAC_SETPOINT,
    ActionType.SET_HVAC_MODE,
    ActionType.SET_DHW_BOOST,
})


class HardLimits:
    """Enforces non-negotiable safety constraints on every Action.

    Usage::

        limits = HardLimits(config)
        safe, reason = limits.validate_action(action, state)
        if not safe:
            logger.warning("action_blocked", reason=reason)
    """

    def __init__(self, config: Config) -> None:
        self._max_setpoint = config.hvac_max_setpoint_c
        self._min_setpoint = config.hvac_min_setpoint_c
        self._max_step = config.hvac_max_step_per_cycle_c
        self._indoor_min = config.indoor_temp_min_winter_c
        self._frost_outdoor = config.hvac_frost_outdoor_c
        self._battery_min = config.battery_soc_min_pct
        self._dhw_min_before_evening_c = 45.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_action(self, action: Action, state: SystemState) -> tuple[bool, str]:
        """Validate an action against all hard limits.

        Args:
            action: The Action the brain wants to execute.
            state: Current unified system state.

        Returns:
            ``(True, "")`` if the action is safe.
            ``(False, reason)`` if blocked — reason explains which limit fired.
        """
        if action.action_type in _SAFE_ACTION_TYPES:
            return True, ""

        indoor = state.heat_pump.indoor_temp_c
        outdoor = state.heat_pump.outdoor_temp_c

        # Rule 1 — indoor below minimum → force_heat MUST pass through
        if action.action_type == ActionType.SET_HVAC_SETPOINT:
            new_sp = action.parameters.get("temperature")
            if new_sp is not None:
                # Always allow if we're trying to heat because indoor is too cold
                if indoor < self._indoor_min and float(new_sp) > state.heat_pump.setpoint_c:
                    return True, ""
                ok, reason = self._check_setpoint(float(new_sp), state)
                if not ok:
                    return False, reason

        # Rule 2 — frost protection: block non-HVAC-emergency interference
        if outdoor < self._frost_outdoor and indoor >= self._indoor_min:
            if action.action_type not in _HVAC_ACTION_TYPES:
                reason = (
                    f"Frost protection active: outdoor={outdoor}°C < "
                    f"threshold={self._frost_outdoor}°C, indoor temp is safe"
                )
                logger.warning("hard_limit_frost_block", action=action.action_type.value, reason=reason)
                return False, reason

        # Battery SOC minimum — never discharge below floor
        if action.action_type == ActionType.SET_BATTERY_POWER:
            if state.battery.soc_pct <= self._battery_min:
                reason = (
                    f"Battery SOC {state.battery.soc_pct}% at or below "
                    f"minimum {self._battery_min}%"
                )
                logger.warning("hard_limit_battery_soc", reason=reason)
                return False, reason

        return True, ""

    def needs_force_heat(self, state: SystemState) -> bool:
        """True if indoor temperature is below the winter minimum.

        When this returns True the watchdog will issue a force_heat action
        regardless of outdoor temperature (safety trumps pump protection).

        Args:
            state: Current system state.

        Returns:
            True if indoor temp is below ``indoor_temp_min_winter_c``.
        """
        return state.heat_pump.indoor_temp_c < self._indoor_min

    def is_frost_protection_active(self, state: SystemState) -> bool:
        """True if outdoor is below frost threshold AND indoor is still safe.

        When active, non-essential actions (appliances, battery writes) are
        blocked to reduce heat-pump load.

        Args:
            state: Current system state.

        Returns:
            True if outdoor frost rule is active (and indoor_min not violated).
        """
        return (
            state.heat_pump.outdoor_temp_c < self._frost_outdoor
            and state.heat_pump.indoor_temp_c >= self._indoor_min
        )

    def clamp_setpoint(self, requested_c: float, current_c: float) -> float:
        """Return the closest safe setpoint to ``requested_c``.

        Applies max/min hard limits AND the 0.5°C-per-cycle step limit.

        Args:
            requested_c: The setpoint the brain wants to set.
            current_c: The current actual setpoint.

        Returns:
            Clamped setpoint within all hard limits.
        """
        # Absolute bounds
        clamped = max(self._min_setpoint, min(self._max_setpoint, requested_c))
        # Per-cycle step limit
        delta = clamped - current_c
        if abs(delta) > self._max_step:
            clamped = current_c + self._max_step * (1 if delta > 0 else -1)
        return round(clamped, 1)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_setpoint(self, new_setpoint: float, state: SystemState) -> tuple[bool, str]:
        current = state.heat_pump.setpoint_c

        if new_setpoint > self._max_setpoint:
            reason = (
                f"Setpoint {new_setpoint}°C exceeds hard limit {self._max_setpoint}°C "
                f"(floor heating overheat protection)"
            )
            logger.warning("hard_limit_setpoint_max", requested=new_setpoint, limit=self._max_setpoint)
            return False, reason

        if new_setpoint < self._min_setpoint:
            reason = (
                f"Setpoint {new_setpoint}°C below hard limit {self._min_setpoint}°C "
                f"(frost protection)"
            )
            logger.warning("hard_limit_setpoint_min", requested=new_setpoint, limit=self._min_setpoint)
            return False, reason

        step = abs(new_setpoint - current)
        if step > self._max_step:
            reason = (
                f"Setpoint change {step:.1f}°C exceeds max step {self._max_step}°C per cycle"
            )
            logger.warning("hard_limit_step", step=step, max_step=self._max_step)
            return False, reason

        return True, ""


# ---------------------------------------------------------------------------
# CapacityTariffGuard
# ---------------------------------------------------------------------------

class CapacityTariffGuard:
    """Prevents unnecessary demand peaks to minimise Belgian capacity tariff costs.

    Belgian capacity tariff (Fluvius, 2026):
    - Calculated on 12-month rolling average of highest monthly quarter-hour peaks.
    - Rate: ~€47.50/kW/year excl. BTW — verify annually at fluvius.be
    - Minimum 2.5 kW always charged.
    - Impact of 1 kW extra peak: ~€3.96/month.

    Three rules (spec section 14.5):
    1. Cooking peak (17:00–18:30 default): no new large appliance starts.
    2. Stagger: minimum 15-min gap between any two appliance starts.
    3. Force-start during cooking peak: prefer waiting until 18:30 if deadline allows.

    GoodWe peak_shaving is NOT configured — this is the only protection.
    """

    COOKING_PEAK_START_DEFAULT = time(17, 0)
    COOKING_PEAK_END_DEFAULT = time(18, 30)
    MIN_GAP_MINUTES = 15
    # Buffer after peak ends before we consider the window fully clear
    _POST_PEAK_BUFFER_MINUTES = 15

    def __init__(self, config: Config) -> None:
        self._start_default = config.cooking_peak_start_default
        self._end_default = config.cooking_peak_end_default
        self._min_gap = config.min_gap_between_starts_min

    def is_cooking_peak(
        self,
        current_time: time,
        pattern_learner: object = None,
        weekday: int = 0,
        **weather_kwargs: object,
    ) -> bool:
        """True if ``current_time`` falls inside the cooking peak protection window.

        Uses PatternLearner's dynamic window when available, falls back to
        the configured defaults when the model isn't trained yet.

        Args:
            current_time: The time to check.
            pattern_learner: Optional trained PatternLearner instance.
            weekday: Day-of-week (0=Mon) for dynamic window lookup.
            **weather_kwargs: Extra context passed to PatternLearner.

        Returns:
            True if in cooking peak.
        """
        start, end = self._get_peak_window(pattern_learner, weekday, **weather_kwargs)
        return start <= current_time <= end

    def can_start_appliance(
        self,
        appliance: ApplianceType,
        last_appliance_start: Optional[datetime],
        is_force_start: bool = False,
        hard_deadline: Optional[time] = None,
        current_time: Optional[time] = None,
        pattern_learner: object = None,
        weekday: int = 0,
        **weather_kwargs: object,
    ) -> tuple[bool, str]:
        """Decide if an appliance can start right now.

        Checks cooking peak and 15-min stagger rule.

        Args:
            appliance: Which appliance to start.
            last_appliance_start: When the last appliance was started (any type).
            is_force_start: True if the deadline has been exceeded.
            hard_deadline: The appliance's non-negotiable deadline.
            current_time: Override for the current time (useful in tests).
            pattern_learner: Optional trained PatternLearner.
            weekday: 0=Mon … 6=Sun.
            **weather_kwargs: Extra context for dynamic peak window.

        Returns:
            ``(True, reason)`` if allowed.
            ``(False, reason)`` if blocked.
        """
        now = current_time or datetime.now().time()
        _, peak_end = self._get_peak_window(pattern_learner, weekday, **weather_kwargs)
        in_peak = self.is_cooking_peak(now, pattern_learner, weekday, **weather_kwargs)

        # ── Stagger check ──────────────────────���──────────────────────
        if last_appliance_start is not None:
            elapsed_min = (datetime.now() - last_appliance_start).total_seconds() / 60
            if elapsed_min < self._min_gap:
                wait = self._min_gap - elapsed_min
                return False, f"15-min stagger: wait {wait:.0f} more minutes"

        # ── Normal (non-force) start ───────────────────────────────────
        if not is_force_start:
            if in_peak:
                return False, f"Cooking peak active ({now.strftime('%H:%M')})"
            return True, "ok"

        # ── Force start during cooking peak ───────────────────────────
        if in_peak:
            # Can we afford to wait until the peak ends?
            wait_until = time(peak_end.hour, peak_end.minute)
            if hard_deadline is not None and hard_deadline >= time(18, 45):
                return False, f"prefer_wait_until_{wait_until.strftime('%H:%M')}"
            # Deadline is too tight — start despite peak
            return True, "forced_despite_peak"

        return True, "forced"

    def next_allowed_start(
        self,
        last_appliance_start: Optional[datetime],
    ) -> datetime:
        """Return the earliest datetime when the next appliance may start.

        Considers only the 15-min stagger rule (cooking peak is checked
        separately by :meth:`can_start_appliance`).

        Args:
            last_appliance_start: When the last appliance started.

        Returns:
            Earliest allowed start datetime.
        """
        if last_appliance_start is None:
            return datetime.now()
        earliest = last_appliance_start.replace(
            minute=last_appliance_start.minute + self._min_gap
            if last_appliance_start.minute + self._min_gap < 60
            else (last_appliance_start.minute + self._min_gap) % 60,
            second=0,
            microsecond=0,
        )
        # Simpler: just add timedelta
        from datetime import timedelta
        return last_appliance_start + timedelta(minutes=self._min_gap)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_peak_window(
        self,
        pattern_learner: object,
        weekday: int,
        **kwargs: object,
    ) -> tuple[time, time]:
        """Return (start, end) of the cooking peak window.

        Falls back to configured defaults when PatternLearner is None or
        not yet trained.
        """
        if pattern_learner is not None and hasattr(pattern_learner, "get_cooking_peak"):
            try:
                return pattern_learner.get_cooking_peak(weekday, **kwargs)
            except Exception:
                pass
        return self._start_default, self._end_default
