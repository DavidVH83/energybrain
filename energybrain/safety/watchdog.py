"""Independent safety watchdog — runs as a separate asyncio task.

Never depends on brain decisions. Checks 6 critical conditions every 5 minutes.
Safety actions bypass the brain and execute directly.
SAFETY_ALARM notifications are never throttled.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable, Optional

from energybrain.config import Config
from energybrain.models import (
    Action,
    ActionType,
    BatteryMode,
    DeviceStatus,
    NotificationType,
    SystemState,
)
from energybrain.persistence.database import DatabaseManager
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

# Watchdog check thresholds (spec section 5.2)
_DHW_EVENING_HOUR = 17          # After 17:00: DHW must be >= 40°C
_DHW_MIN_TEMP_C = 40.0
_BATTERY_CRITICAL_SOC_PCT = 8.0
_HVAC_IDLE_MAX_HOURS = 4.0      # Alert if HVAC off for 4+ hours in cold weather
_HVAC_IDLE_OUTDOOR_THRESHOLD_C = 5.0
_BRAIN_STALE_MINUTES = 10       # Alert if brain made no decision for 10+ minutes
_INDOOR_MIN_C = 17.0            # Must match HardLimits.indoor_temp_min_winter_c
_INDOOR_OUTDOOR_MAX_FOR_CHECK_C = 15.0  # Only alert when outdoor <= 15°C


class Watchdog:
    """Independent safety monitor for EnergyBrain.

    Runs a separate asyncio task (``run_forever``) that never stops.
    Calls ``check_all`` every 5 minutes regardless of brain state.

    Safety actions are returned as a list and must be executed by the caller.
    The caller is responsible for actually sending notifications.

    Usage::

        watchdog = Watchdog(config, db)
        asyncio.create_task(watchdog.run_forever(state_provider))
    """

    INTERVAL_SECONDS = 300

    def __init__(
        self,
        config: Config,
        db: DatabaseManager,
        on_safety_action: Optional[Callable[[Action], None]] = None,
    ) -> None:
        self._config = config
        self._db = db
        self._on_safety_action = on_safety_action
        self._last_brain_decision: Optional[datetime] = None
        self._last_hvac_active: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def record_brain_decision(self) -> None:
        """Call this from the orchestrator after every decision cycle."""
        self._last_brain_decision = datetime.now()

    def record_hvac_active(self) -> None:
        """Call this whenever HVAC reports heating/cooling active."""
        self._last_hvac_active = datetime.now()

    async def run_forever(
        self,
        state_provider: Callable[[], Optional[SystemState]],
    ) -> None:
        """Run watchdog checks indefinitely as a separate asyncio task.

        Args:
            state_provider: Zero-argument callable that returns the current
                SystemState, or None if no state is available yet.
        """
        logger.info("watchdog_started", interval_s=self.INTERVAL_SECONDS)
        while True:
            await asyncio.sleep(self.INTERVAL_SECONDS)
            state = state_provider()
            if state is None:
                logger.debug("watchdog_skip_no_state")
                continue
            try:
                actions = await self.check_all(state)
                for action in actions:
                    logger.warning(
                        "watchdog_safety_action",
                        action_type=action.action_type.value,
                        reason=action.reason,
                    )
                    if self._on_safety_action:
                        self._on_safety_action(action)
            except Exception as exc:
                logger.error("watchdog_check_error", error=str(exc))

    async def check_all(self, state: SystemState) -> list[Action]:
        """Run all 6 safety checks and return required actions.

        Args:
            state: Current unified system state snapshot.

        Returns:
            List of Actions that must be executed immediately.
            Empty list means all checks passed.
        """
        actions: list[Action] = []

        # Check 1 — Indoor temperature minimum
        action = self._check_indoor_temp(state)
        if action:
            actions.append(action)
            await self._db.log_safety_event(
                "indoor_temp_min",
                "CRITICAL",
                f"Indoor {state.heat_pump.indoor_temp_c}°C < {_INDOOR_MIN_C}°C",
                action_taken=action.action_type.value,
                notification_sent=True,
            )

        # Check 2 — DHW temperature after 17:00
        action = self._check_dhw_temp(state)
        if action:
            actions.append(action)
            await self._db.log_safety_event(
                "dhw_temp_low",
                "WARNING",
                f"DHW {state.heat_pump.dhw_temp_c}°C < {_DHW_MIN_TEMP_C}°C after 17:00",
                action_taken=action.action_type.value,
                notification_sent=True,
            )

        # Check 3 — Battery SOC critical
        action = self._check_battery_soc(state)
        if action:
            actions.append(action)
            await self._db.log_safety_event(
                "battery_soc_critical",
                "CRITICAL",
                f"Battery SoC {state.battery.soc_pct}% < {_BATTERY_CRITICAL_SOC_PCT}%",
                action_taken=action.action_type.value,
                notification_sent=True,
            )

        # Check 4 — HVAC idle in cold conditions
        action = self._check_hvac_idle(state)
        if action:
            actions.append(action)
            await self._db.log_safety_event(
                "hvac_idle_cold",
                "WARNING",
                f"HVAC idle > {_HVAC_IDLE_MAX_HOURS}h, outdoor {state.heat_pump.outdoor_temp_c}°C",
                action_taken=action.action_type.value,
                notification_sent=False,
            )

        # Check 5 — Brain stale (no recent decision)
        action = self._check_brain_stale()
        if action:
            actions.append(action)
            await self._db.log_safety_event(
                "brain_stale",
                "WARNING",
                f"Brain has not made a decision for > {_BRAIN_STALE_MINUTES} minutes",
                action_taken="send_notification",
                notification_sent=True,
            )

        # Check 6 — Marstek CT clamp disconnected
        action = self._check_ct_clamp(state)
        if action:
            actions.append(action)

        return actions

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_indoor_temp(self, state: SystemState) -> Optional[Action]:
        """Check 1: indoor < 17°C AND outdoor <= 15°C → force heat."""
        if (
            state.heat_pump.indoor_temp_c < _INDOOR_MIN_C
            and state.heat_pump.outdoor_temp_c <= _INDOOR_OUTDOOR_MAX_FOR_CHECK_C
        ):
            return Action(
                action_type=ActionType.SET_HVAC_SETPOINT,
                target_entity="climate.anna",
                parameters={"temperature": _INDOOR_MIN_C + 1.0},  # Slightly above min
                priority=100,
                reason=(
                    f"WATCHDOG: indoor {state.heat_pump.indoor_temp_c}°C "
                    f"< {_INDOOR_MIN_C}°C threshold"
                ),
            )
        return None

    def _check_dhw_temp(self, state: SystemState) -> Optional[Action]:
        """Check 2: DHW < 40°C after 17:00 → force DHW boost."""
        now_hour = datetime.now().hour
        dhw = state.heat_pump.dhw_temp_c
        if dhw is None:
            return None
        if now_hour >= _DHW_EVENING_HOUR and dhw < _DHW_MIN_TEMP_C:
            return Action(
                action_type=ActionType.SET_DHW_BOOST,
                target_entity="select.opentherm_ssw_modus",
                parameters={"option": "boost"},
                priority=90,
                reason=(
                    f"WATCHDOG: DHW {dhw}°C < {_DHW_MIN_TEMP_C}°C "
                    f"after {_DHW_EVENING_HOUR}:00"
                ),
            )
        return None

    def _check_battery_soc(self, state: SystemState) -> Optional[Action]:
        """Check 3: Battery SoC < 8% → force passive mode (stop discharge).

        Skip when status is ERROR (CT disconnected) because SoC reads 0.0
        as a fallback and would cause a false critical alarm.
        """
        if (
            state.battery.status == DeviceStatus.ONLINE
            and state.battery.soc_pct < _BATTERY_CRITICAL_SOC_PCT
        ):
            return Action(
                action_type=ActionType.SET_BATTERY_MODE,
                target_entity="select.marstek_venuse_operating_mode",
                parameters={"option": BatteryMode.PASSIVE.value},
                priority=95,
                reason=(
                    f"WATCHDOG: battery SoC {state.battery.soc_pct}% "
                    f"< critical {_BATTERY_CRITICAL_SOC_PCT}%"
                ),
                is_stub=not state.battery.write_enabled,
            )
        return None

    def _check_hvac_idle(self, state: SystemState) -> Optional[Action]:
        """Check 4: HVAC idle > 4 h AND outdoor < 5°C → force heat warning."""
        if self._last_hvac_active is None:
            return None
        outdoor = state.heat_pump.outdoor_temp_c
        if outdoor >= _HVAC_IDLE_OUTDOOR_THRESHOLD_C:
            return None
        idle_hours = (datetime.now() - self._last_hvac_active).total_seconds() / 3600
        if idle_hours > _HVAC_IDLE_MAX_HOURS:
            return Action(
                action_type=ActionType.SET_HVAC_SETPOINT,
                target_entity="climate.anna",
                parameters={"temperature": 19.0},
                priority=70,
                reason=(
                    f"WATCHDOG: HVAC idle {idle_hours:.1f}h, "
                    f"outdoor {outdoor}°C (pump frost protection)"
                ),
            )
        return None

    def _check_brain_stale(self) -> Optional[Action]:
        """Check 5: Brain no decision > 10 min → warning notification."""
        if self._last_brain_decision is None:
            return None
        stale_min = (datetime.now() - self._last_brain_decision).total_seconds() / 60
        if stale_min > _BRAIN_STALE_MINUTES:
            return Action(
                action_type=ActionType.SEND_NOTIFICATION,
                target_entity="notify",
                parameters={
                    "type": NotificationType.SAFETY_ALARM.value,
                    "message": f"EnergyBrain has not run for {stale_min:.0f} minutes — process may be dead",
                },
                priority=80,
                reason=f"WATCHDOG: brain stale {stale_min:.0f} min",
            )
        return None

    def _check_ct_clamp(self, state: SystemState) -> Optional[Action]:
        """Check 6: Marstek CT clamp disconnected → warning notification."""
        # CT clamp status is stored as battery attribute — check via DeviceStatus
        # The MarstekAgent sets battery.status=ERROR when ct_connected=off
        if state.battery.status == DeviceStatus.ERROR:
            return Action(
                action_type=ActionType.SEND_NOTIFICATION,
                target_entity="notify",
                parameters={
                    "type": NotificationType.SAFETY_ALARM.value,
                    "message": (
                        "Marstek CT clamp disconnected — battery cannot measure grid load. "
                        "Check binary_sensor.marstek_venuse_ct_connected in HA."
                    ),
                },
                priority=60,
                reason="WATCHDOG: Marstek CT clamp disconnected",
            )
        return None
