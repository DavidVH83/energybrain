"""DayPlanner — creates optimal day plan at 06:30, updates every 15 min.

Two-pass per cycle:
  Pass 1: Solar optimization — schedule tasks in surplus windows by priority
  Pass 2: Deadline enforcement — guarantee tasks always run by hard deadline

Priority: DHW > Dishwasher > Washing machine > Dryer > Battery > HVAC preload
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Optional

from energybrain.models import (
    ApplianceState,
    ApplianceType,
    DayPlan,
    ScheduledTask,
    SurplusWindow,
    SystemState,
)
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

APPLIANCE_DEADLINES: dict[ApplianceType, dict] = {
    ApplianceType.DISHWASHER:      {"max_wait_h": 4,  "hard_deadline": time(20, 0)},
    ApplianceType.WASHING_MACHINE: {"max_wait_h": 6,  "hard_deadline": time(20, 0)},
    ApplianceType.DRYER:           {"max_wait_h": 8,  "hard_deadline": time(21, 0)},
}

# Minimum surplus to consider an appliance run worthwhile
APPLIANCE_MIN_SURPLUS_W: dict[ApplianceType, float] = {
    ApplianceType.DISHWASHER:      1200.0,
    ApplianceType.WASHING_MACHINE: 1500.0,
    ApplianceType.DRYER:           2000.0,
}

DHW_MIN_SURPLUS_W = 800.0
DHW_ESTIMATED_DURATION_H = 1.0

# Morning notification thresholds
NOTIFICATION_MIN_SURPLUS_HOURS = 1.5
NOTIFICATION_MIN_SURPLUS_W = 1500.0

# Forecast deviation threshold for plan update
DEVIATION_PCT = 0.10

# Appliance probability threshold for morning notification mention
LOADING_PROBABILITY_THRESHOLD = 0.5


class DayPlanner:
    """Builds and maintains the 24-hour energy day plan."""

    def __init__(self) -> None:
        self._log = get_logger("day_planner")

    def create_day_plan(self, state: SystemState) -> DayPlan:
        """Full day plan. Called at 06:30."""
        forecast = state.weather
        hourly = forecast.hourly[:24]

        from energybrain.intelligence.pv_forecaster import PVForecaster
        # Use surplus windows already on forecast if available, else recalculate
        surplus_windows = self._get_surplus_windows(state)
        tasks = self._schedule_tasks(surplus_windows, state)

        plan = DayPlan(
            date=datetime.now().replace(hour=0, minute=0, second=0, microsecond=0),
            total_pv_forecast_kwh=forecast.daily_pv_kwh,
            surplus_windows=surplus_windows,
            scheduled_tasks=tasks,
        )
        self._log.info(
            "day_plan_created",
            pv_kwh=round(forecast.daily_pv_kwh, 2),
            surplus_windows=len(surplus_windows),
            tasks=len(tasks),
        )
        return plan

    def update_plan(self, current_plan: DayPlan, state: SystemState) -> DayPlan:
        """Update plan if forecast deviated > 10%. Called every 15 min."""
        new_kwh = state.weather.daily_pv_kwh
        old_kwh = current_plan.total_pv_forecast_kwh

        if old_kwh > 0 and abs(new_kwh - old_kwh) / old_kwh > DEVIATION_PCT:
            self._log.info(
                "day_plan_update_triggered",
                old_kwh=round(old_kwh, 2),
                new_kwh=round(new_kwh, 2),
            )
            return self.create_day_plan(state)

        # Still check deadlines — force-start if needed
        updated_tasks = list(current_plan.scheduled_tasks)
        for appliance, config in APPLIANCE_DEADLINES.items():
            app_state = state.appliances.get(appliance)
            if app_state is None or app_state.is_running:
                continue
            if app_state.waiting_since is None:
                continue
            force, reason = self.should_force_start(appliance, app_state.waiting_since)
            if force:
                # Mark existing task as forced or create new forced task
                updated_tasks = self._mark_forced(updated_tasks, appliance, reason)

        return DayPlan(
            date=current_plan.date,
            total_pv_forecast_kwh=current_plan.total_pv_forecast_kwh,
            surplus_windows=current_plan.surplus_windows,
            scheduled_tasks=updated_tasks,
            week_strategy_note=current_plan.week_strategy_note,
            generated_at=current_plan.generated_at,
        )

    def should_force_start(
        self, appliance: ApplianceType, waiting_since: datetime
    ) -> tuple[bool, str]:
        """Return (should_force, reason).

        Force if: waited > max_wait_h OR current_time >= hard_deadline.
        """
        config = APPLIANCE_DEADLINES.get(appliance)
        if config is None:
            return False, ""

        now = datetime.now()
        waited_hours = (now - waiting_since).total_seconds() / 3600
        hard_deadline = config["hard_deadline"]
        max_wait_h = config["max_wait_h"]

        if waited_hours >= max_wait_h:
            return True, f"waited {waited_hours:.1f}h (max {max_wait_h}h)"
        if now.time() >= hard_deadline:
            return True, f"hard deadline {hard_deadline} reached"
        return False, ""

    def build_morning_notification(self, plan: DayPlan, state: SystemState) -> str:
        """Build proactive push notification if meaningful surplus expected.

        Only sent if:
         - Surplus window >= NOTIFICATION_MIN_SURPLUS_HOURS
         - At least 1 appliance loading probability > 0.5
        """
        good_windows = [
            w for w in plan.surplus_windows
            if w.duration_hours >= NOTIFICATION_MIN_SURPLUS_HOURS
            and w.avg_surplus_w >= NOTIFICATION_MIN_SURPLUS_W
        ]
        if not good_windows:
            return ""

        best = max(good_windows, key=lambda w: w.total_energy_kwh)
        kwh = best.total_energy_kwh
        avg_w = best.avg_surplus_w

        # Find appliances likely to be used today
        likely_appliances = self._likely_appliances(state, plan)
        if not likely_appliances:
            return ""

        appliance_icons = {
            ApplianceType.DISHWASHER:      ("vaatwasser", "🍽️"),
            ApplianceType.WASHING_MACHINE: ("Wasmachine", "🧺"),
            ApplianceType.DRYER:           ("Droger", "👕"),
        }
        lines = [
            "Goede zon verwacht vandaag!",
            f"Surplus: {best.start_hour:02d}:00-{best.end_hour:02d}:00 "
            f"(gem. {avg_w:.0f}W, ~{kwh:.1f}kWh)",
            "",
            "Als je klaar staat starten we automatisch:",
        ]
        for a in likely_appliances:
            name, icon = appliance_icons.get(a, (a.value, ""))
            lines.append(f"{icon} {name}")
        lines.append("")
        lines.append("Zet toestellen klaar en activeer remote start.")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_surplus_windows(self, state: SystemState) -> list[SurplusWindow]:
        """Extract or rebuild surplus windows from forecast."""
        hourly = state.weather.hourly[:24]
        windows: list[SurplusWindow] = []
        start = None
        surplus_vals: list[float] = []
        grid_baseline = 500.0  # W

        for h in hourly:
            surplus = h.pv_estimated_w - grid_baseline
            if surplus >= DHW_MIN_SURPLUS_W:
                if start is None:
                    start = h.hour
                surplus_vals.append(surplus)
            else:
                if start is not None and surplus_vals:
                    end = h.hour
                    windows.append(SurplusWindow(
                        start_hour=start,
                        end_hour=end,
                        avg_surplus_w=sum(surplus_vals) / len(surplus_vals),
                        total_energy_kwh=sum(surplus_vals) / 1000.0,
                    ))
                    start = None
                    surplus_vals = []

        if start is not None and surplus_vals:
            end = (hourly[-1].hour + 1) % 24 if hourly else 0
            windows.append(SurplusWindow(
                start_hour=start,
                end_hour=end,
                avg_surplus_w=sum(surplus_vals) / len(surplus_vals),
                total_energy_kwh=sum(surplus_vals) / 1000.0,
            ))
        return windows

    def _schedule_tasks(
        self, surplus_windows: list[SurplusWindow], state: SystemState
    ) -> list[ScheduledTask]:
        """Two-pass scheduling: solar-optimized first, then deadline enforcement."""
        tasks: list[ScheduledTask] = []
        now = datetime.now()

        # DHW — always priority 1
        tasks.append(self._schedule_dhw(surplus_windows, now))

        # Appliances — pass 1: fit in surplus; pass 2: deadline enforcement
        priority = 2
        for appliance in [
            ApplianceType.DISHWASHER,
            ApplianceType.WASHING_MACHINE,
            ApplianceType.DRYER,
        ]:
            app_state = state.appliances.get(appliance)
            if app_state is None or app_state.is_running:
                priority += 1
                continue

            config = APPLIANCE_DEADLINES[appliance]
            min_surplus = APPLIANCE_MIN_SURPLUS_W[appliance]
            deadline = config["hard_deadline"]
            max_wait_h = config["max_wait_h"]

            planned_start, is_forced = self._find_start_time(
                surplus_windows, min_surplus, now, deadline, max_wait_h, app_state
            )
            tasks.append(ScheduledTask(
                name=appliance.value,
                appliance_type=appliance,
                planned_start=planned_start,
                min_surplus_w=min_surplus,
                estimated_duration_hours=1.5,
                hard_deadline=deadline,
                max_wait_hours=float(max_wait_h),
                priority=priority,
                is_forced=is_forced,
            ))
            priority += 1

        return tasks

    def _schedule_dhw(
        self, surplus_windows: list[SurplusWindow], now: datetime
    ) -> ScheduledTask:
        """Schedule DHW boost in first good surplus window."""
        best_window = next(
            (w for w in surplus_windows if w.avg_surplus_w >= DHW_MIN_SURPLUS_W),
            None,
        )
        if best_window:
            planned = now.replace(
                hour=best_window.start_hour, minute=0, second=0, microsecond=0
            )
            if planned < now:
                planned += timedelta(days=1)
        else:
            planned = now.replace(hour=10, minute=0, second=0, microsecond=0)
            if planned < now:
                planned += timedelta(hours=1)

        return ScheduledTask(
            name="dhw_boost",
            appliance_type=None,
            planned_start=planned,
            min_surplus_w=DHW_MIN_SURPLUS_W,
            estimated_duration_hours=DHW_ESTIMATED_DURATION_H,
            hard_deadline=time(18, 0),
            max_wait_hours=12.0,
            priority=1,
            is_forced=False,
        )

    def _find_start_time(
        self,
        surplus_windows: list[SurplusWindow],
        min_surplus_w: float,
        now: datetime,
        deadline: time,
        max_wait_h: int,
        app_state: ApplianceState,
    ) -> tuple[datetime, bool]:
        """Return (planned_start, is_forced)."""
        # Check deadline first
        if app_state.waiting_since:
            force, _ = self.should_force_start(app_state.appliance_type, app_state.waiting_since)
            if force:
                return now, True

        # Try to find a surplus window
        for window in surplus_windows:
            if window.avg_surplus_w >= min_surplus_w:
                planned = now.replace(
                    hour=window.start_hour, minute=0, second=0, microsecond=0
                )
                if planned < now:
                    planned += timedelta(days=1)
                deadline_dt = now.replace(
                    hour=deadline.hour, minute=deadline.minute, second=0, microsecond=0
                )
                if planned <= deadline_dt:
                    return planned, False

        # No good window — schedule at deadline
        deadline_dt = now.replace(
            hour=deadline.hour, minute=deadline.minute, second=0, microsecond=0
        )
        if deadline_dt < now:
            deadline_dt += timedelta(days=1)
        return deadline_dt, True

    @staticmethod
    def _mark_forced(
        tasks: list[ScheduledTask], appliance: ApplianceType, reason: str
    ) -> list[ScheduledTask]:
        """Return updated task list with appliance marked as forced."""
        result = []
        for t in tasks:
            if t.appliance_type == appliance and not t.is_forced:
                result.append(ScheduledTask(
                    name=t.name,
                    appliance_type=t.appliance_type,
                    planned_start=datetime.now(),
                    min_surplus_w=t.min_surplus_w,
                    estimated_duration_hours=t.estimated_duration_hours,
                    hard_deadline=t.hard_deadline,
                    max_wait_hours=t.max_wait_hours,
                    priority=t.priority,
                    is_forced=True,
                ))
            else:
                result.append(t)
        return result

    def _likely_appliances(
        self, state: SystemState, plan: DayPlan
    ) -> list[ApplianceType]:
        """Return appliances with loading probability > threshold (from tasks)."""
        # Tasks that are not forced are solar-optimized — include them in notification
        likely = []
        for task in plan.scheduled_tasks:
            if task.appliance_type is None:
                continue
            app_state = state.appliances.get(task.appliance_type)
            if app_state and not app_state.is_running:
                likely.append(task.appliance_type)
        return likely
