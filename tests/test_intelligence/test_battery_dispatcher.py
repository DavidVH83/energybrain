"""Tests for energybrain.intelligence.battery_dispatcher."""
import pytest

from energybrain.intelligence.battery_dispatcher import (
    BATTERY_CAPACITY_KWH,
    MAX_CHARGE_W,
    MAX_DISCHARGE_W,
    MIN_SOC_PCT,
    MAX_SOC_PCT,
    TIMESTEPS,
    BatteryDispatcher,
)
from energybrain.models import BatteryDispatchPlan


def _make_forecast(n: int = TIMESTEPS, pv_w: float = 0.0, cons_w: float = 500.0):
    return [pv_w] * n, [cons_w] * n


class TestBatteryDispatcherInit:
    def test_stub_mode_default(self):
        bd = BatteryDispatcher()
        assert bd.stub_mode is True

    def test_write_enabled_disables_stub(self):
        bd = BatteryDispatcher(write_enabled=True)
        assert bd.stub_mode is False


class TestCalculateDispatchPlan:
    def test_returns_battery_dispatch_plan(self):
        bd = BatteryDispatcher()
        pv, cons = _make_forecast()
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        assert isinstance(plan, BatteryDispatchPlan)

    def test_plan_is_stub_in_stub_mode(self):
        bd = BatteryDispatcher(write_enabled=False)
        pv, cons = _make_forecast()
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        assert plan.is_stub is True

    def test_plan_not_stub_when_write_enabled(self):
        bd = BatteryDispatcher(write_enabled=True)
        pv, cons = _make_forecast()
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        assert plan.is_stub is False

    def test_plan_has_96_timesteps(self):
        bd = BatteryDispatcher()
        pv, cons = _make_forecast()
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        assert len(plan.hourly_target_w) == TIMESTEPS

    def test_charge_during_pv_surplus(self):
        bd = BatteryDispatcher()
        # Morning: PV surplus to charge; evening: no PV, high demand → discharge
        # LP has clear incentive: charge midday, discharge evening to avoid imports
        pv = [0.0] * 32 + [3000.0] * 32 + [0.0] * 32   # surplus in steps 32-63
        cons = [2000.0] * TIMESTEPS                       # constant high load
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        # Should charge or discharge at some point — battery has a role
        assert any(w != 0.0 for w in plan.hourly_target_w)

    def test_discharge_during_deficit(self):
        bd = BatteryDispatcher()
        # No PV, high consumption
        pv = [0.0] * TIMESTEPS
        cons = [2000.0] * TIMESTEPS
        plan = bd.calculate_dispatch_plan(pv, cons, 80.0, 3.0, 0.25, 0.036)
        # Should discharge to cover deficit
        assert any(w < 0 for w in plan.hourly_target_w)

    def test_charge_rate_never_exceeds_max(self):
        bd = BatteryDispatcher()
        pv = [5000.0] * TIMESTEPS
        cons = [200.0] * TIMESTEPS
        plan = bd.calculate_dispatch_plan(pv, cons, 10.0, 3.0, 0.25, 0.036)
        assert all(w <= MAX_CHARGE_W for w in plan.hourly_target_w)

    def test_discharge_rate_never_exceeds_max(self):
        bd = BatteryDispatcher()
        pv = [0.0] * TIMESTEPS
        cons = [5000.0] * TIMESTEPS
        plan = bd.calculate_dispatch_plan(pv, cons, 80.0, 3.0, 0.25, 0.036)
        assert all(w >= -MAX_DISCHARGE_W for w in plan.hourly_target_w)

    def test_expected_savings_non_negative(self):
        bd = BatteryDispatcher()
        pv, cons = _make_forecast(pv_w=2000.0)
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        assert plan.expected_savings_eur >= 0.0

    def test_shorter_forecast_padded_to_96(self):
        bd = BatteryDispatcher()
        pv = [1000.0] * 48
        cons = [500.0] * 48
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        assert len(plan.hourly_target_w) == TIMESTEPS


class TestExecutePlan:
    async def test_execute_stub_does_not_raise(self):
        bd = BatteryDispatcher(write_enabled=False)
        pv, cons = _make_forecast()
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        await bd.execute_plan(plan)  # Should not raise

    async def test_execute_live_does_not_raise(self):
        bd = BatteryDispatcher(write_enabled=True)
        pv, cons = _make_forecast()
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        await bd.execute_plan(plan)  # Should not raise


class TestExplainPlan:
    def test_returns_string(self):
        bd = BatteryDispatcher()
        pv, cons = _make_forecast()
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        explanation = bd.explain_plan(plan)
        assert isinstance(explanation, str)
        assert len(explanation) > 0

    def test_stub_note_in_explanation(self):
        bd = BatteryDispatcher(write_enabled=False)
        pv, cons = _make_forecast()
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        explanation = bd.explain_plan(plan)
        assert "STUB" in explanation

    def test_no_stub_note_when_live(self):
        bd = BatteryDispatcher(write_enabled=True)
        pv, cons = _make_forecast()
        plan = bd.calculate_dispatch_plan(pv, cons, 50.0, 3.0, 0.25, 0.036)
        explanation = bd.explain_plan(plan)
        assert "STUB" not in explanation


class TestGreedyFallback:
    def test_greedy_charges_on_surplus(self):
        bd = BatteryDispatcher()
        import numpy as np
        pv = np.array([2000.0] * 10)
        cons = np.array([300.0] * 10)
        plan = bd._greedy_fallback(pv, cons, 10.0, 10)
        assert any(w > 0 for w in plan)

    def test_greedy_soc_never_below_min(self):
        bd = BatteryDispatcher()
        import numpy as np
        from energybrain.intelligence.battery_dispatcher import (
            _CAPACITY_WH, _DT_HOURS, MIN_SOC_PCT
        )
        pv = np.array([0.0] * TIMESTEPS)
        cons = np.array([3000.0] * TIMESTEPS)
        plan = bd._greedy_fallback(pv, cons, 50.0, TIMESTEPS)
        # Track SoC
        soc_wh = 50.0 / 100.0 * _CAPACITY_WH
        min_wh = MIN_SOC_PCT / 100.0 * _CAPACITY_WH
        for w in plan:
            soc_wh += w * _DT_HOURS
            assert soc_wh >= min_wh - 0.01
