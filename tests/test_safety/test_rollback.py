"""Tests for energybrain.safety.rollback."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from energybrain.models import Action, ActionResult, ActionType
from energybrain.safety.rollback import RollbackEntry, RollbackManager


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _action(entity: str = "climate.anna", rollback_min: int = None) -> Action:
    return Action(
        action_type=ActionType.SET_HVAC_SETPOINT,
        target_entity=entity,
        parameters={"temperature": 22.0},
        rollback_after_minutes=rollback_min,
    )


def _rollback_action(entity: str = "climate.anna") -> Action:
    return Action(
        action_type=ActionType.SET_HVAC_SETPOINT,
        target_entity=entity,
        parameters={"temperature": 20.0},
    )


def _make_executor(success: bool = True) -> MagicMock:
    executor = MagicMock()
    executor.execute = AsyncMock(
        return_value=ActionResult(
            success=success,
            action=_rollback_action(),
        )
    )
    return executor


# ---------------------------------------------------------------------------
# register()
# ---------------------------------------------------------------------------

class TestRegister:
    def test_registers_entry(self):
        rm = RollbackManager()
        orig = _action(rollback_min=60)
        rb = _rollback_action()
        rm.register(orig, rb)
        assert rm.pending_count == 1

    def test_raises_when_no_rollback_minutes(self):
        rm = RollbackManager()
        orig = _action(rollback_min=None)
        with pytest.raises(ValueError, match="rollback_after_minutes"):
            rm.register(orig, _rollback_action())

    def test_execute_at_is_correct(self):
        rm = RollbackManager()
        orig = _action(rollback_min=30)
        before = datetime.now()
        rm.register(orig, _rollback_action())
        entry = rm._pending[0]
        expected = before + timedelta(minutes=30)
        assert abs((entry.execute_at - expected).total_seconds()) < 2

    def test_multiple_registrations(self):
        rm = RollbackManager()
        for i in range(3):
            rm.register(_action(entity=f"entity_{i}", rollback_min=60), _rollback_action())
        assert rm.pending_count == 3


# ---------------------------------------------------------------------------
# check_and_execute()
# ---------------------------------------------------------------------------

class TestCheckAndExecute:
    async def test_executes_overdue_rollback(self):
        rm = RollbackManager()
        orig = _action(rollback_min=1)
        rm.register(orig, _rollback_action())
        # Force the entry to be overdue
        rm._pending[0].execute_at = datetime.now() - timedelta(seconds=1)
        executor = _make_executor(success=True)
        results = await rm.check_and_execute(executor)
        assert len(results) == 1
        assert results[0].success is True

    async def test_does_not_execute_future_rollback(self):
        rm = RollbackManager()
        orig = _action(rollback_min=120)
        rm.register(orig, _rollback_action())
        # Entry is in the future — should not execute
        executor = _make_executor()
        results = await rm.check_and_execute(executor)
        assert len(results) == 0

    async def test_removes_executed_entry(self):
        rm = RollbackManager()
        rm.register(_action(rollback_min=1), _rollback_action())
        rm._pending[0].execute_at = datetime.now() - timedelta(seconds=1)
        await rm.check_and_execute(_make_executor())
        assert rm.pending_count == 0

    async def test_keeps_future_entries_after_executing_overdue(self):
        rm = RollbackManager()
        # One overdue, one future
        rm.register(_action(entity="e1", rollback_min=1), _rollback_action("e1"))
        rm.register(_action(entity="e2", rollback_min=120), _rollback_action("e2"))
        rm._pending[0].execute_at = datetime.now() - timedelta(seconds=1)
        await rm.check_and_execute(_make_executor())
        assert rm.pending_count == 1
        assert rm._pending[0].rollback_action.target_entity == "e2"

    async def test_continues_when_executor_raises(self):
        rm = RollbackManager()
        rm.register(_action(rollback_min=1), _rollback_action())
        rm._pending[0].execute_at = datetime.now() - timedelta(seconds=1)
        bad_executor = MagicMock()
        bad_executor.execute = AsyncMock(side_effect=RuntimeError("connection failed"))
        # Should not raise — logs the error and continues
        results = await rm.check_and_execute(bad_executor)
        assert results == []

    async def test_rollback_executes_after_configured_timeout(self):
        """Spec scenario: brain raises setpoint → rollback after N minutes."""
        rm = RollbackManager()
        raise_action = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 22.0},
            reason="solar preloading",
            rollback_after_minutes=240,
        )
        restore_action = Action(
            action_type=ActionType.SET_HVAC_SETPOINT,
            target_entity="climate.anna",
            parameters={"temperature": 20.0},
            reason="rollback: solar preloading ended",
        )
        rm.register(raise_action, restore_action)
        assert rm.pending_count == 1

        # Simulate 4 hours passing
        rm._pending[0].execute_at = datetime.now() - timedelta(seconds=1)
        executor = _make_executor()
        results = await rm.check_and_execute(executor)

        assert len(results) == 1
        executor.execute.assert_awaited_once()
        call_args = executor.execute.await_args[0][0]
        assert call_args.parameters["temperature"] == 20.0


# ---------------------------------------------------------------------------
# cancel()
# ---------------------------------------------------------------------------

class TestCancel:
    def test_cancels_matching_entry(self):
        rm = RollbackManager()
        rm.register(_action("climate.anna", rollback_min=60), _rollback_action("climate.anna"))
        rm.register(_action("other.entity", rollback_min=60), _rollback_action("other.entity"))
        count = rm.cancel("climate.anna")
        assert count == 1
        assert rm.pending_count == 1
        assert rm._pending[0].rollback_action.target_entity == "other.entity"

    def test_cancel_nonexistent_entity_returns_zero(self):
        rm = RollbackManager()
        count = rm.cancel("nonexistent")
        assert count == 0

    def test_cancel_all(self):
        rm = RollbackManager()
        for i in range(5):
            rm.register(_action(entity=f"e{i}", rollback_min=60), _rollback_action(f"e{i}"))
        count = rm.cancel_all()
        assert count == 5
        assert rm.pending_count == 0


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------

class TestInspection:
    def test_pending_count_zero_initially(self):
        assert RollbackManager().pending_count == 0

    def test_pending_for_entity(self):
        rm = RollbackManager()
        rm.register(_action("climate.anna", rollback_min=60), _rollback_action("climate.anna"))
        rm.register(_action("other", rollback_min=60), _rollback_action("other"))
        entries = rm.pending_for_entity("climate.anna")
        assert len(entries) == 1

    def test_next_due_returns_soonest(self):
        rm = RollbackManager()
        rm.register(_action("e1", rollback_min=120), _rollback_action("e1"))
        rm.register(_action("e2", rollback_min=30), _rollback_action("e2"))
        rm.register(_action("e3", rollback_min=60), _rollback_action("e3"))
        soonest = rm.next_due()
        assert soonest is not None
        assert soonest.rollback_action.target_entity == "e2"

    def test_next_due_returns_none_when_empty(self):
        assert RollbackManager().next_due() is None
