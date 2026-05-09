"""Rollback manager — tracks reversible actions and auto-undoes them after timeout.

Example: brain sets HVAC to 22°C for solar preloading at 11:00.
If PV forecast was wrong: rolls back to previous setpoint after 4 hours.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from energybrain.models import Action, ActionResult
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class RollbackEntry:
    """A pending rollback that will execute at ``execute_at``."""
    original_action: Action
    rollback_action: Action
    execute_at: datetime
    registered_at: datetime = field(default_factory=datetime.now)
    executed: bool = False


class RollbackManager:
    """Tracks reversible actions and auto-rolls them back after their timeout.

    Every :class:`~energybrain.models.Action` with ``rollback_after_minutes``
    set should be registered here immediately after execution.

    The orchestrator calls :meth:`check_and_execute` every cycle; overdue
    rollbacks are executed in the order they were registered.

    Usage::

        rm = RollbackManager()
        rm.register(original_action, rollback_action)
        # ... in the main loop:
        results = await rm.check_and_execute(executor)
    """

    def __init__(self) -> None:
        self._pending: list[RollbackEntry] = []

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        original_action: Action,
        rollback_action: Action,
    ) -> None:
        """Register an action for automatic rollback.

        Args:
            original_action: The action that was executed. Must have
                ``rollback_after_minutes`` set.
            rollback_action: The action to execute when the timeout expires.

        Raises:
            ValueError: If ``original_action.rollback_after_minutes`` is None.
        """
        if original_action.rollback_after_minutes is None:
            raise ValueError(
                f"Cannot register rollback for action without rollback_after_minutes: "
                f"{original_action.action_type.value} on {original_action.target_entity}"
            )
        execute_at = datetime.now() + timedelta(
            minutes=original_action.rollback_after_minutes
        )
        entry = RollbackEntry(
            original_action=original_action,
            rollback_action=rollback_action,
            execute_at=execute_at,
        )
        self._pending.append(entry)
        logger.info(
            "rollback_registered",
            action=original_action.action_type.value,
            entity=original_action.target_entity,
            rollback_in_min=original_action.rollback_after_minutes,
            execute_at=execute_at.isoformat(),
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def check_and_execute(
        self,
        executor: "ActionExecutor",
    ) -> list[ActionResult]:
        """Execute any overdue rollbacks.

        Should be called every orchestrator cycle (every 60 s).

        Args:
            executor: Any object with ``async execute(action) → ActionResult``.

        Returns:
            List of ActionResults for each rollback that was executed.
        """
        now = datetime.now()
        due = [e for e in self._pending if not e.executed and e.execute_at <= now]
        results: list[ActionResult] = []

        for entry in due:
            try:
                result = await executor.execute(entry.rollback_action)
                entry.executed = True
                results.append(result)
                logger.info(
                    "rollback_executed",
                    action=entry.rollback_action.action_type.value,
                    entity=entry.rollback_action.target_entity,
                    original_reason=entry.original_action.reason,
                    success=result.success,
                )
            except Exception as exc:
                logger.error(
                    "rollback_failed",
                    action=entry.rollback_action.action_type.value,
                    entity=entry.rollback_action.target_entity,
                    error=str(exc),
                )

        # Purge executed entries to keep the list small
        self._pending = [e for e in self._pending if not e.executed]
        return results

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel(self, target_entity: str) -> int:
        """Cancel all pending rollbacks for a specific entity.

        Useful when a user manually changes a setting, making the
        original rollback stale.

        Args:
            target_entity: HA entity ID whose pending rollbacks should
                be cancelled.

        Returns:
            Number of rollbacks cancelled.
        """
        before = len(self._pending)
        self._pending = [
            e for e in self._pending
            if e.rollback_action.target_entity != target_entity
        ]
        cancelled = before - len(self._pending)
        if cancelled:
            logger.info("rollback_cancelled", entity=target_entity, count=cancelled)
        return cancelled

    def cancel_all(self) -> int:
        """Cancel all pending rollbacks.

        Returns:
            Number of rollbacks cancelled.
        """
        count = len(self._pending)
        self._pending.clear()
        if count:
            logger.info("rollback_cancel_all", count=count)
        return count

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    @property
    def pending_count(self) -> int:
        """Number of pending (not yet executed) rollbacks."""
        return len(self._pending)

    def pending_for_entity(self, target_entity: str) -> list[RollbackEntry]:
        """Return all pending rollback entries for a specific entity.

        Args:
            target_entity: HA entity ID.

        Returns:
            List of pending RollbackEntry objects.
        """
        return [
            e for e in self._pending
            if e.rollback_action.target_entity == target_entity
        ]

    def next_due(self) -> Optional[RollbackEntry]:
        """Return the rollback entry that will execute soonest.

        Returns:
            The soonest pending RollbackEntry, or None if none pending.
        """
        active = [e for e in self._pending if not e.executed]
        return min(active, key=lambda e: e.execute_at) if active else None
