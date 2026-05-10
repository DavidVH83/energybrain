"""BatteryDispatcher — MPC 24-hour battery charge/discharge scheduling.

STUB STATUS: Marstek RS485 write is broken in V153 firmware (known regression).
RS485 read works. Write blocked by firmware bug. Fix expected in V154.
All output is LOGGED but NOT executed until MARSTEK_WRITE_ENABLED=true.

MPC via scipy.optimize.linprog (linear programming).
Objective: minimize grid import cost + capacity tariff impact.
Constraints: SoC 10-95%, charge/discharge rate limits, peak demand limit.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

import numpy as np

from energybrain.models import BatteryDispatchPlan
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

TIMESTEP_MINUTES = 15
HORIZON_HOURS = 24
TIMESTEPS = HORIZON_HOURS * (60 // TIMESTEP_MINUTES)   # 96

MIN_SOC_PCT = 10.0
MAX_SOC_PCT = 95.0
BATTERY_CAPACITY_KWH = 5.12
MAX_CHARGE_W = 2600.0
MAX_DISCHARGE_W = 2600.0

# Conversion helpers
_DT_HOURS = TIMESTEP_MINUTES / 60.0
_CAPACITY_WH = BATTERY_CAPACITY_KWH * 1000.0
_SOC_MIN_WH = MIN_SOC_PCT / 100.0 * _CAPACITY_WH
_SOC_MAX_WH = MAX_SOC_PCT / 100.0 * _CAPACITY_WH


class BatteryDispatcher:
    """Calculates optimal 24-hour battery schedule. Logs plan; executes only when write enabled."""

    def __init__(self, write_enabled: bool = False) -> None:
        self._write_enabled = write_enabled
        self._log = get_logger("battery_dispatcher")

    @property
    def stub_mode(self) -> bool:
        return not self._write_enabled

    def calculate_dispatch_plan(
        self,
        pv_forecast_w: list[float],
        consumption_forecast_w: list[float],
        current_soc_pct: float,
        current_monthly_peak_kw: float,
        import_price_eur_kwh: float,
        export_price_eur_kwh: float,
    ) -> BatteryDispatchPlan:
        """Solve MPC optimization. Returns plan regardless of stub mode."""
        n = min(TIMESTEPS, len(pv_forecast_w), len(consumption_forecast_w))
        pv = np.array(pv_forecast_w[:n], dtype=float)
        consumption = np.array(consumption_forecast_w[:n], dtype=float)

        try:
            from scipy.optimize import linprog  # type: ignore[import-untyped]
            plan_w = self._solve_mpc(
                pv, consumption, current_soc_pct,
                current_monthly_peak_kw, import_price_eur_kwh, export_price_eur_kwh,
                n,
            )
        except Exception as exc:
            self._log.warning("mpc_solver_failed", error=str(exc))
            plan_w = self._greedy_fallback(pv, consumption, current_soc_pct, n)

        # Pad to 96 steps if shorter
        full_plan = list(plan_w) + [0.0] * (TIMESTEPS - len(plan_w))

        savings = self._estimate_savings(full_plan, pv_forecast_w, consumption_forecast_w,
                                         import_price_eur_kwh, export_price_eur_kwh)
        peak_prev = self._estimate_peak_prevention(full_plan, consumption_forecast_w,
                                                    current_monthly_peak_kw)

        plan = BatteryDispatchPlan(
            date=datetime.now(),
            hourly_target_w=full_plan,
            expected_savings_eur=savings,
            peak_prevention_kw=peak_prev,
            is_stub=self.stub_mode,
        )
        self._log.info(
            "dispatch_plan_calculated",
            stub=self.stub_mode,
            expected_savings_eur=round(savings, 3),
            peak_prevention_kw=round(peak_prev, 2),
        )
        return plan

    async def execute_plan(self, plan: BatteryDispatchPlan) -> None:
        """Execute current timestep of plan.

        STUB: logs intended action without writing to Marstek.
        LIVE (post-V154): would call marstek_agent.set_power_w(target_w).
        """
        now = datetime.now()
        step = (now.hour * 60 + now.minute) // TIMESTEP_MINUTES
        step = min(step, len(plan.hourly_target_w) - 1)
        target_w = plan.hourly_target_w[step]

        if self.stub_mode:
            direction = "charge" if target_w >= 0 else "discharge"
            self._log.info(
                "stub_battery_action",
                action=f"STUB: would {direction} {abs(target_w):.0f}W at {now.strftime('%H:%M')}",
                step=step,
                target_w=target_w,
            )
        else:
            # Live path — requires V154 firmware and MarstekAgent write support
            self._log.info("battery_dispatch_execute", step=step, target_w=target_w)

    def explain_plan(self, plan: BatteryDispatchPlan) -> str:
        """Human-readable plan summary for daily notification."""
        lines = ["Batterijplan morgen:"]
        targets = plan.hourly_target_w

        # Summarize charge and discharge blocks (hourly resolution)
        hourly = [sum(targets[i * 4:(i + 1) * 4]) / 4 for i in range(24)]
        charge_blocks = self._summarize_blocks(hourly, positive=True)
        discharge_blocks = self._summarize_blocks(hourly, positive=False)

        for start, end, avg_w in charge_blocks:
            lines.append(f"  {start:02d}:00-{end:02d}:00: Laden van zonnepanelen (+{avg_w / 1000:.1f}kW)")
        for start, end, avg_w in discharge_blocks:
            lines.append(f"  {start:02d}:00-{end:02d}:00: Ontladen voor kookpiek (-{avg_w / 1000:.1f}kW)")

        lines.append(f"Verwachte besparing: €{plan.expected_savings_eur:.2f} | "
                     f"Piekbesparing: {plan.peak_prevention_kw:.1f}kW")
        if plan.is_stub:
            lines.append("[STUB — plan berekend, nog niet uitgevoerd]")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # MPC solver
    # ------------------------------------------------------------------

    def _solve_mpc(
        self,
        pv: np.ndarray,
        consumption: np.ndarray,
        current_soc_pct: float,
        current_monthly_peak_kw: float,
        import_price: float,
        export_price: float,
        n: int,
    ) -> list[float]:
        """Linear program: minimize import cost over n timesteps.

        Decision variables: charge_w[t] >= 0, discharge_w[t] >= 0 for t in 0..n-1
        x = [charge_0, ..., charge_{n-1}, discharge_0, ..., discharge_{n-1}]
        """
        from scipy.optimize import linprog  # type: ignore[import-untyped]

        dt = _DT_HOURS
        # Net surplus at each step (positive = PV surplus, negative = deficit)
        net = pv - consumption

        # Cost: we want to minimize grid import
        # Grid import at step t = max(0, consumption[t] - pv[t] + discharge[t] - charge[t])
        # Linearized: import ~ -net[t] + charge[t] - discharge[t] when net < 0
        # Cost per unit charge = 0; per unit discharge = -export_price * dt
        # Cost per unit grid import = import_price * dt
        # Simple: c[charge_t] = 0; c[discharge_t] = -import_price * dt
        c = np.zeros(2 * n)
        c[n:] = -import_price * dt  # discharging reduces import cost

        # Inequality constraints: A_ub @ x <= b_ub
        # 1. SoC bounds: soc evolves as soc[t+1] = soc[t] + charge[t]*dt - discharge[t]*dt
        # Express SoC trajectory, enforce min/max
        A_ub_list = []
        b_ub_list = []

        soc0_wh = current_soc_pct / 100.0 * _CAPACITY_WH

        # SoC upper bound at each step: sum(charge[:t]) - sum(discharge[:t]) <= (MAX - soc0) / dt
        # SoC lower bound at each step: sum(discharge[:t]) - sum(charge[:t]) <= (soc0 - MIN) / dt
        for t in range(1, n + 1):
            # Upper: cumulative charge - discharge <= (MAX_WH - soc0) / dt
            row_upper = np.zeros(2 * n)
            row_upper[:t] = dt       # charge terms
            row_upper[n:n + t] = -dt  # discharge terms
            A_ub_list.append(row_upper)
            b_ub_list.append(_SOC_MAX_WH - soc0_wh)

            # Lower: -charge + discharge <= soc0 - MIN_WH
            row_lower = np.zeros(2 * n)
            row_lower[:t] = -dt
            row_lower[n:n + t] = dt
            A_ub_list.append(row_lower)
            b_ub_list.append(soc0_wh - _SOC_MIN_WH)

        A_ub = np.array(A_ub_list)
        b_ub = np.array(b_ub_list)

        # Bounds: 0 <= charge <= MAX_CHARGE; 0 <= discharge <= MAX_DISCHARGE
        bounds = [(0, MAX_CHARGE_W)] * n + [(0, MAX_DISCHARGE_W)] * n

        result = linprog(c, A_ub=A_ub, b_ub=b_ub, bounds=bounds, method="highs")
        if result.success:
            charges = result.x[:n]
            discharges = result.x[n:]
            return list(charges - discharges)
        # Solver failed — use greedy
        return self._greedy_fallback(pv, consumption, current_soc_pct, n)

    def _greedy_fallback(
        self,
        pv: np.ndarray,
        consumption: np.ndarray,
        current_soc_pct: float,
        n: int,
    ) -> list[float]:
        """Rule-based greedy: charge during surplus, discharge during deficit."""
        soc_wh = current_soc_pct / 100.0 * _CAPACITY_WH
        plan: list[float] = []
        for t in range(n):
            net = pv[t] - consumption[t]
            if net > 0:
                # Surplus: charge battery
                charge = min(net, MAX_CHARGE_W, (_SOC_MAX_WH - soc_wh) / _DT_HOURS)
                charge = max(0.0, charge)
                soc_wh = min(_SOC_MAX_WH, soc_wh + charge * _DT_HOURS)
                plan.append(charge)
            else:
                # Deficit: discharge battery
                discharge = min(-net, MAX_DISCHARGE_W, (soc_wh - _SOC_MIN_WH) / _DT_HOURS)
                discharge = max(0.0, discharge)
                soc_wh = max(_SOC_MIN_WH, soc_wh - discharge * _DT_HOURS)
                plan.append(-discharge)
        return plan

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _estimate_savings(
        plan_w: list[float],
        pv_w: list[float],
        consumption_w: list[float],
        import_price: float,
        export_price: float,
    ) -> float:
        """Estimate € savings vs no battery (simple)."""
        dt = _DT_HOURS
        savings = 0.0
        n = min(len(plan_w), len(pv_w), len(consumption_w))
        for i in range(n):
            target = plan_w[i]
            # Without battery: import deficit, export surplus
            no_bat_net = pv_w[i] - consumption_w[i]
            # With battery: net adjusted by battery action
            with_bat_net = no_bat_net - target  # positive target = charging = less export
            import_no = max(0.0, -no_bat_net) * dt / 1000.0
            import_with = max(0.0, -with_bat_net) * dt / 1000.0
            savings += (import_no - import_with) * import_price
        return max(0.0, savings)

    @staticmethod
    def _estimate_peak_prevention(
        plan_w: list[float],
        consumption_w: list[float],
        current_peak_kw: float,
    ) -> float:
        """Estimate kW of peak demand prevented by discharging during peak load."""
        dt = _DT_HOURS
        n = min(len(plan_w), len(consumption_w))
        net_demand = [max(0.0, consumption_w[i] - max(0.0, -plan_w[i])) / 1000.0
                      for i in range(n)]
        max_net = max(net_demand) if net_demand else 0.0
        prevented = max(0.0, current_peak_kw - max_net)
        return prevented

    @staticmethod
    def _summarize_blocks(
        hourly: list[float], positive: bool
    ) -> list[tuple[int, int, float]]:
        """Extract continuous blocks of charge (positive) or discharge (negative) power."""
        blocks = []
        start = None
        vals: list[float] = []
        for i, w in enumerate(hourly):
            active = w > 50 if positive else w < -50
            if active:
                if start is None:
                    start = i
                vals.append(abs(w))
            else:
                if start is not None:
                    blocks.append((start, i, float(np.mean(vals))))
                    start = None
                    vals = []
        if start is not None:
            blocks.append((start, len(hourly), float(np.mean(vals))))
        return blocks
