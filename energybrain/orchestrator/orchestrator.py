"""Orchestrator — main EnergyBrain control loop.

Three concurrent asyncio tasks:
  Task 1 — Realtime (60 s):   collect state, validate, execute, persist
  Task 2 — Day planner (15 min): update DayPlan on forecast deviation
  Task 3 — Watchdog (5 min):  independent safety monitor

Scheduled jobs (time-based logic inside realtime loop):
  21:00  PVForecaster.update_calibration + OutcomeTracker.log_outcome(pv)
  21:30  Daily summary notification + BatteryDispatcher stub log
  02:00  check_drift + ThermalModel.update_model
  02:30  WeekStrategist.calculate_strategy
  02:00 Sun  PatternLearner.update_patterns
  03:00  hourly aggregate + DB cleanup
  06:30  DayPlanner.create_day_plan + morning notification
  07:00 Mon  Week strategy notification
  07:00 1st  Monthly accuracy report notification
  Every hour  BatteryDispatcher.execute_plan (STUB)

Startup (StartupRecovery):
  1. GoodWe → GENERAL operating mode
  2. Marstek mode check → log if not AUTO
  3. Anna schema check → must be 'off'
"""
from __future__ import annotations

import asyncio
import json
import traceback
from datetime import datetime, timedelta
from typing import Optional

from energybrain.agents.energy_price_agent import EnergyPriceAgent
from energybrain.agents.goodwe_agent import GoodWeAgent
from energybrain.agents.ha_control_agent import HAControlAgent
from energybrain.agents.heat_pump_agent import HeatPumpAgent
from energybrain.agents.home_connect_agent import HomeConnectAgent
from energybrain.agents.marstek_agent import MarstekAgent
from energybrain.agents.notification_agent import NotificationAgent
from energybrain.agents.p1_agent import P1Agent
from energybrain.agents.weather_agent import WeatherAgent
from energybrain.config import Config
from energybrain.intelligence.battery_dispatcher import BatteryDispatcher
from energybrain.intelligence.day_planner import DayPlanner
from energybrain.intelligence.oscillation_detector import OscillationDetector
from energybrain.intelligence.outcome_tracker import OutcomeTracker
from energybrain.intelligence.pattern_learner import PatternLearner
from energybrain.intelligence.pv_forecaster import PVForecaster
from energybrain.intelligence.thermal_model import ThermalModel
from energybrain.intelligence.week_strategist import WeekStrategist
from energybrain.models import (
    ActionType,
    ApplianceType,
    BatteryMode,
    DayPlan,
    NotificationType,
    SystemState,
    WeekStrategy,
)
from energybrain.persistence.database import DatabaseManager
from energybrain.persistence.learning_store import LearningStore
from energybrain.persistence.state_store import StateStore
from energybrain.safety.hard_limits import HardLimits
from energybrain.safety.rollback import RollbackManager
from energybrain.safety.watchdog import Watchdog
from energybrain.utils.ha_client import HAClient
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

REALTIME_INTERVAL = 60
DAYPLAN_INTERVAL = 15 * 60
WATCHDOG_INTERVAL = 5 * 60


class Orchestrator:
    """Main EnergyBrain control loop. Three concurrent asyncio tasks."""

    def __init__(self, config: Config, db: DatabaseManager) -> None:
        self._config = config
        self._db = db
        self._log = get_logger("orchestrator")

        # HA client + agents
        self._ha = HAClient(config.ha_url, config.ha_token)
        self._goodwe = GoodWeAgent(self._ha)
        self._p1 = P1Agent(self._ha)
        self._marstek = MarstekAgent(self._ha, config)
        self._heat_pump = HeatPumpAgent(self._ha)
        self._home_connect = HomeConnectAgent(self._ha)
        self._ha_control = HAControlAgent(self._ha)
        self._weather = WeatherAgent(config)
        self._prices = EnergyPriceAgent(config)
        self._notifier = NotificationAgent(self._ha, config)

        # Persistence
        self._state_store = StateStore(db)
        self._learning_store = LearningStore(db)

        # Intelligence
        self._thermal_model = ThermalModel()
        self._pv_forecaster = PVForecaster()
        self._pattern_learner = PatternLearner()
        self._outcome_tracker = OutcomeTracker()
        self._oscillation_detector = OscillationDetector()
        self._battery_dispatcher = BatteryDispatcher(
            write_enabled=config.marstek_write_enabled
        )
        self._day_planner = DayPlanner()
        self._week_strategist = WeekStrategist()
        self._rollback = RollbackManager()
        self._hard_limits = HardLimits(config)
        self._watchdog = Watchdog(config, db)

        # Runtime state
        self._current_state: Optional[SystemState] = None
        self._day_plan: Optional[DayPlan] = None
        self._week_strategy: Optional[WeekStrategy] = None
        self._dispatch_plan = None
        self._last_decision_at: Optional[datetime] = None
        self._scheduled_done: dict[str, str] = {}  # job_key → date/hour it last ran
        self._last_action_str: str = ""  # shown in dashboard status banner
        self._executed_today: list[str] = []  # log of today's executed actions
        self._executed_today_date: Optional[str] = None  # reset daily
        self._last_control_state = None  # ControlState, refreshed every cycle

        # Capacity tariff tracking (Belgian quarter-hour peak)
        self._capacity_samples: list[float] = []   # grid import W, last 15 readings
        self._capacity_monthly_peak_w: float = 0.0
        self._capacity_peak_month: str = ""         # YYYY-MM, reset each month
        self._capacity_window_start: Optional[datetime] = None

        # Statistics accumulation (reset at midnight)
        self._stats_pv_today_kwh: float = 0.0      # read from GoodWe daily sensor
        self._stats_accumulated_date: Optional[str] = None  # YYYY-MM-DD

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Initialize, run startup checks, then launch all tasks."""
        self._log.info("orchestrator_starting")
        await self._ha.open()
        await self._startup_recovery()
        await self._load_models()

        tasks = [
            asyncio.create_task(self._realtime_loop(), name="realtime"),
            asyncio.create_task(self._dayplan_loop(), name="dayplan"),
            asyncio.create_task(self._watchdog.run_forever(self._get_state), name="watchdog"),
        ]
        try:
            await asyncio.gather(*tasks)
        except Exception as exc:
            self._log.error("orchestrator_crashed", error=str(exc))
            raise
        finally:
            await self._ha.close()

    async def stop(self) -> None:
        """Graceful shutdown."""
        self._log.info("orchestrator_stopping")
        await self._ha.close()

    # ------------------------------------------------------------------
    # Task 1 — Realtime loop (60 s)
    # ------------------------------------------------------------------

    async def _realtime_loop(self) -> None:
        while True:
            try:
                await self._realtime_cycle()
            except Exception as exc:
                self._log.error(
                    "realtime_cycle_error",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    traceback=traceback.format_exc(),
                )
            await asyncio.sleep(REALTIME_INTERVAL)

    async def _realtime_cycle(self) -> None:
        now = datetime.now()

        # 1. Collect SystemState
        state = await self._collect_state()
        self._current_state = state
        self._last_decision_at = now

        # 2. Persist
        await self._state_store.save_state(state)
        await self._db.write_heartbeat()

        # 3. Rollback manager tick
        rollback_actions = self._rollback.due_actions(now)
        for action in rollback_actions:
            ok, reason = self._hard_limits.validate_action(action, state)
            if ok:
                await self._execute_action(action, state)

        # 4. Execute due DayPlan tasks
        if self._day_plan:
            await self._execute_due_tasks(state, now)

        # 4.5. Temperature control — lower setpoint on hot days
        await self._apply_temperature_control(state)

        # 4.6. Capacity tariff — track 15-min grid import average
        await self._track_capacity_tariff(state, now)

        # 5. Scheduled jobs
        await self._run_scheduled_jobs(state, now)

        # 6. Update HA status banner
        await self._update_ha_status(state, now)

        self._log.debug(
            "realtime_cycle_done",
            pv_w=round(state.pv.power_w),
            soc=round(state.battery.soc_pct),
            indoor_c=round(state.heat_pump.indoor_temp_c, 1),
        )

    def _log_executed_action(self, description: str, now: datetime) -> None:
        """Track an executed action for today's execution log (resets at midnight)."""
        today = now.strftime("%Y-%m-%d")
        if self._executed_today_date != today:
            self._executed_today = []
            self._executed_today_date = today
        entry = f"{now.strftime('%H:%M')} {description}"
        if entry not in self._executed_today:
            self._executed_today.append(entry)
        # Keep last 6 entries, join in 255-char budget
        self._last_action_str = entry

    def _build_live_flow(self, state: SystemState) -> str:
        """Build live energy flow string: PV → Battery / House / Grid."""
        pv_w = state.pv.power_w
        battery_w = state.battery.power_w      # positive = charging
        grid_w = state.grid.power_w            # positive = importing

        # house = pv - battery + grid (energy balance)
        house_w = max(0.0, pv_w - battery_w + grid_w)

        battery_label = (
            f"🔋 Laadt {battery_w:+.0f}W" if battery_w > 50
            else f"🔋 Ontlaadt {battery_w:.0f}W" if battery_w < -50
            else "🔋 Standby"
        )
        grid_label = (
            f"🔌 Export {-grid_w:.0f}W" if grid_w < -50
            else f"📥 Import {grid_w:.0f}W" if grid_w > 50
            else "🔌 Neutraal"
        )
        dhw = "🚿 DHW boost" if state.heat_pump.dhw_boost_active else ""
        parts = [
            f"☀️ PV {pv_w:.0f}W",
            battery_label,
            f"🏠 Huis {house_w:.0f}W",
            grid_label,
        ]
        if dhw:
            parts.append(dhw)
        return " · ".join(parts)

    async def _update_ha_status(self, state: SystemState, now: datetime) -> None:
        """Write live status + plan overview to HA input_text helpers."""
        try:
            # ── Status line ──────────────────────────────────────────────────
            pv_w = round(state.pv.power_w)
            soc = round(state.battery.soc_pct)
            net_w = round(state.grid.power_w)
            net_sign = "+" if net_w >= 0 else ""
            status = f"Actief | PV: {pv_w}W | SoC: {soc}% | Net: {net_sign}{net_w}W"

            # ── Last action ──────────────────────────────────────────────────
            last_action = self._last_action_str or "—"

            # ── Today plan (scheduled tasks) ─────────────────────────────────
            tasks = self._day_plan.scheduled_tasks if self._day_plan else []
            if tasks:
                task_strs = []
                for t in tasks[:5]:
                    icon = "🔴" if t.is_forced else "🟡"
                    task_strs.append(f"{icon} {t.name} {t.planned_start.strftime('%H:%M')}")
                today_plan = " · ".join(task_strs)
            else:
                today_plan = "Geen dagplan beschikbaar"

            # ── Next action ──────────────────────────────────────────────────
            upcoming = [t for t in tasks if t.planned_start > now]
            if upcoming:
                nxt = upcoming[0]
                forced_note = " (deadline!)" if nxt.is_forced else ""
                next_action = f"{nxt.name} om {nxt.planned_start.strftime('%H:%M')}{forced_note}"
            else:
                next_action = "—"

            # ── Plan summary (PV forecast + surplus windows) ─────────────────
            if self._day_plan:
                pv_kwh = self._day_plan.total_pv_forecast_kwh
                windows = self._day_plan.surplus_windows
                if windows:
                    win_strs = [
                        f"{w.start_hour}:00-{w.end_hour}:00 ({w.avg_surplus_w/1000:.1f}kW)"
                        for w in windows[:3]
                    ]
                    plan_summary = (
                        f"☀️ {pv_kwh:.1f}kWh | "
                        f"Surplus: {' | '.join(win_strs)} | "
                        f"Plan: {self._day_plan.generated_at.strftime('%H:%M')}"
                    )[:255]
                else:
                    plan_summary = f"☀️ {pv_kwh:.1f}kWh voorspeld | Geen surplus vensters"
            else:
                plan_summary = "Dagplan nog niet aangemaakt (wordt 06:30 gemaakt)"

            # ── Executed today ────────────────────────────────────────────────
            today = now.strftime("%Y-%m-%d")
            if self._executed_today_date != today:
                self._executed_today = []
                self._executed_today_date = today
            if self._executed_today:
                executed_today = " · ".join(self._executed_today[-6:])[:255]
            else:
                executed_today = "Nog niets uitgevoerd vandaag"

            await self._ha_control.update_status(
                status, last_action, today_plan, next_action,
                plan_summary, executed_today,
            )

            # ── Live energy flow ─────────────────────────────────────────────
            live_flow = self._build_live_flow(state)
            await self._ha.call_service(
                "input_text", "set_value",
                entity_id="input_text.energybrain_live_flow",
                value=live_flow[:255],
            )
        except Exception as exc:
            self._log.warning("ha_status_update_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Task 2 — Day planner loop (15 min)
    # ------------------------------------------------------------------

    async def _dayplan_loop(self) -> None:
        while True:
            await asyncio.sleep(DAYPLAN_INTERVAL)
            try:
                if self._current_state and self._day_plan:
                    self._day_plan = self._day_planner.update_plan(
                        self._day_plan, self._current_state
                    )
                    if self._current_state:
                        self._current_state = SystemState(
                            **{**self._current_state.__dict__, "day_plan": self._day_plan}
                        )
            except Exception as exc:
                self._log.error("dayplan_update_error", error=str(exc))

    # ------------------------------------------------------------------
    # Scheduled jobs
    # ------------------------------------------------------------------

    async def _run_scheduled_jobs(self, state: SystemState, now: datetime) -> None:
        h = now.hour
        m = now.minute
        date_str = now.strftime("%Y-%m-%d")
        weekday = now.weekday()  # 0=Mon
        day_of_month = now.day

        # 22:00 — Daily statistics + financial summary
        if h == 22 and m < 1:
            await self._job_once(f"statistics_{date_str}", self._job_statistics, state)

        # 21:00 — PV calibration + outcome logging
        if h == 21 and m < 1:
            await self._job_once(f"pv_calibration_{date_str}", self._job_pv_calibration, state)

        # 21:30 — Daily summary
        if h == 21 and 30 <= m < 31:
            await self._job_once(f"daily_summary_{date_str}", self._job_daily_summary, state)

        # 02:00 — Drift check + thermal model update
        if h == 2 and m < 1:
            await self._job_once(f"drift_thermal_{date_str}", self._job_drift_and_thermal, state)

        # 02:00 Sunday — PatternLearner update
        if h == 2 and m < 1 and weekday == 6:
            await self._job_once(f"patterns_sunday_{date_str}", self._job_pattern_update, state)

        # 02:30 — WeekStrategist
        if h == 2 and 30 <= m < 31:
            await self._job_once(f"week_strategy_{date_str}", self._job_week_strategy, state)

        # 03:00 — DB maintenance
        if h == 3 and m < 1:
            await self._job_once(f"db_maintenance_{date_str}", self._job_db_maintenance, state)

        # 06:30 — Day plan creation
        if h == 6 and 30 <= m < 31:
            await self._job_once(f"day_plan_{date_str}", self._job_create_day_plan, state)

        # 07:00 Monday — Week strategy notification
        if h == 7 and m < 1 and weekday == 0:
            await self._job_once(f"week_notify_{date_str}", self._job_week_notification, state)

        # 07:00 1st of month — Monthly report
        if h == 7 and m < 1 and day_of_month == 1:
            await self._job_once(f"monthly_report_{date_str}", self._job_monthly_report, state)

        # Every hour — BatteryDispatcher execute
        if m < 1:
            await self._job_battery_dispatch_execute()

    async def _job_once(self, key: str, coro, *args) -> None:
        """Run a job at most once per key (prevents duplicate runs in same minute)."""
        if key in self._scheduled_done:
            return
        self._scheduled_done[key] = datetime.now().isoformat()
        # Prune old keys to avoid unbounded growth
        if len(self._scheduled_done) > 500:
            oldest = sorted(self._scheduled_done.items(), key=lambda x: x[1])[:100]
            for k, _ in oldest:
                del self._scheduled_done[k]
        try:
            await coro(*args)
        except Exception as exc:
            self._log.error("scheduled_job_failed", job=key, error=str(exc))

    async def _job_pv_calibration(self, state: SystemState) -> None:
        predicted = state.weather.daily_pv_kwh
        actual = state.pv.daily_energy_kwh
        cal_row = await self._state_store.build_pv_calibration_row(
            datetime.now(), predicted, actual
        )
        self._pv_forecaster.update_calibration(**cal_row)
        await self._learning_store.save_pv_forecaster(self._pv_forecaster)
        self._log.info("pv_calibration_done", predicted=round(predicted, 2), actual=round(actual, 2))

    async def _job_daily_summary(self, state: SystemState) -> None:
        stub_info = self._battery_dispatcher.explain_plan(self._dispatch_plan) \
            if self._dispatch_plan else ""
        msg = (
            f"Dagresultaat: PV {state.pv.daily_energy_kwh:.1f}kWh | "
            f"Import {state.grid.daily_import_kwh:.1f}kWh | "
            f"Export {state.grid.daily_export_kwh:.1f}kWh\n{stub_info}"
        )
        await self._notifier.send(
            NotificationType.DAILY_SUMMARY, "EnergyBrain dagresultaat", msg
        )

    async def _job_drift_and_thermal(self, state: SystemState) -> None:
        drift = self._outcome_tracker.check_drift()
        drifted = [m for m, d in drift.items() if d]
        if drifted:
            await self._notifier.send(
                NotificationType.MODEL_DRIFT,
                "EnergyBrain: modeldrift gedetecteerd",
                (
                    f"EnergyBrain merkt dat voorspellingen minder kloppen ({', '.join(drifted)}). "
                    "Zijn uw gewoonten veranderd? Het systeem past zich aan."
                ),
            )

        observations = await self._state_store.build_thermal_observations()
        params = self._thermal_model.update_model(observations)
        self._outcome_tracker.trigger_thermal_model_upgrade(self._thermal_model)
        await self._learning_store.save_thermal_model(self._thermal_model)
        self._log.info("thermal_model_updated", r2=round(params.r2_score, 3))

    async def _job_pattern_update(self, state: SystemState) -> None:
        training_data = await self._state_store.build_pattern_training_data()
        self._pattern_learner.update_patterns(training_data)
        await self._learning_store.save_pattern_learner(self._pattern_learner)
        self._log.info("pattern_learner_updated", days=self._pattern_learner._days_of_data)

    async def _job_week_strategy(self, state: SystemState) -> None:
        forecast_7d = self._build_week_forecast(state)
        self._week_strategy = self._week_strategist.calculate_strategy(
            self._thermal_model, self._pv_forecaster,
            self._oscillation_detector, forecast_7d
        )
        self._log.info(
            "week_strategy_calculated",
            heating=self._week_strategy.heating_days,
            neutral=self._week_strategy.neutral_days,
        )

    async def _job_db_maintenance(self, state: SystemState) -> None:
        deleted = await self._db.cleanup_old_states(self._config.db_retention_days)
        deleted_h = await self._db.cleanup_old_hourly(self._config.db_hourly_retention_years)
        self._log.info("db_maintenance_done", deleted_states=deleted, deleted_hourly=deleted_h)

    async def _job_create_day_plan(self, state: SystemState) -> None:
        self._day_plan = self._day_planner.create_day_plan(state)

        # Build battery dispatch plan
        pv_96 = self._expand_to_96(state.weather.hourly[:24])
        cons_96 = [state.grid.power_w + state.pv.power_w] * 96
        self._dispatch_plan = self._battery_dispatcher.calculate_dispatch_plan(
            pv_forecast_w=pv_96,
            consumption_forecast_w=cons_96,
            current_soc_pct=state.battery.soc_pct,
            current_monthly_peak_kw=0.0,
            import_price_eur_kwh=self._config.static_import_price_eur_kwh,
            export_price_eur_kwh=self._config.static_export_price_eur_kwh,
        )

        # Morning notification
        msg = self._day_planner.build_morning_notification(self._day_plan, state)
        if msg:
            await self._notifier.send(
                NotificationType.SOLAR_OPPORTUNITY, "EnergyBrain: dagplan", msg
            )

    async def _job_week_notification(self, state: SystemState) -> None:
        if not self._week_strategy:
            return
        msg = self._week_strategist.explain_strategy(self._week_strategy)
        await self._notifier.send(
            NotificationType.WEEK_STRATEGY, "EnergyBrain weekstrategie", msg
        )

    async def _job_monthly_report(self, state: SystemState) -> None:
        report = self._outcome_tracker.get_accuracy_report(period_days=30)
        importances = self._pattern_learner.get_feature_importances()
        msg = (
            f"Maandrapport EnergyBrain:\n"
            f"DHW: {report.dhw_accuracy_pct:.0f}% | "
            f"Toestellen: {report.appliance_loading_accuracy_pct:.0f}% | "
            f"PV: {report.pv_forecast_accuracy_pct:.0f}% | "
            f"Kookpiek: {report.cooking_peak_accuracy_pct:.0f}%\n"
            f"Voorspellingen: {report.total_predictions}"
        )
        await self._notifier.send(
            NotificationType.MONTHLY_REPORT, "EnergyBrain maandrapport", msg
        )

    async def _job_battery_dispatch_execute(self) -> None:
        if self._dispatch_plan:
            await self._battery_dispatcher.execute_plan(self._dispatch_plan)

    # ------------------------------------------------------------------
    # State collection
    # ------------------------------------------------------------------

    async def _collect_state(self) -> SystemState:
        pv, battery, grid, heat_pump, appliances, control = await asyncio.gather(
            self._goodwe.collect(),
            self._marstek.collect(),
            self._p1.collect(),
            self._heat_pump.collect(),
            self._home_connect.collect(),
            self._ha_control.collect(),
            return_exceptions=True,
        )
        weather = await self._weather.collect(calibration_factor=1.0)
        prices = await self._prices.collect()

        # Handle agent errors gracefully — use last known state or defaults
        from energybrain.models import (
            BatteryState, GridState, HeatPumpState, PVState, HVACMode, DeviceStatus
        )
        if isinstance(pv, Exception):
            self._log.warning("goodwe_agent_error", error=str(pv))
            pv = PVState(power_w=0.0, daily_energy_kwh=0.0, status=DeviceStatus.ERROR)
        if isinstance(battery, Exception):
            self._log.warning("marstek_agent_error", error=str(battery))
            battery = BatteryState(soc_pct=50.0, power_w=0.0, temperature_c=25.0, status=DeviceStatus.ERROR)
        if isinstance(grid, Exception):
            self._log.warning("p1_agent_error", error=str(grid))
            grid = GridState(power_w=0.0, daily_import_kwh=0.0, daily_export_kwh=0.0, status=DeviceStatus.ERROR)
        if isinstance(heat_pump, Exception):
            self._log.warning("heat_pump_agent_error", error=str(heat_pump))
            heat_pump = HeatPumpState(
                indoor_temp_c=20.0, outdoor_temp_c=10.0, setpoint_c=20.0,
                hvac_mode=HVACMode.HEAT, dhw_boost_active=False, status=DeviceStatus.ERROR
            )
        if isinstance(appliances, Exception):
            self._log.warning("home_connect_agent_error", error=str(appliances))
            appliances = {}
        if isinstance(control, Exception):
            control = None
        self._last_control_state = control  # store for temperature control etc.

        return SystemState(
            pv=pv,
            battery=battery,
            grid=grid,
            heat_pump=heat_pump,
            appliances=appliances,
            weather=weather,
            prices=prices,
            day_plan=self._day_plan,
            week_strategy=self._week_strategy,
        )

    def _get_state(self) -> Optional[SystemState]:
        return self._current_state

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def _execute_due_tasks(self, state: SystemState, now: datetime) -> None:
        """Check DayPlan scheduled tasks and execute any that are due.

        Safety guards (all must pass before starting an appliance):
          1. remote_start_allowed = True  (machine is loaded and program set)
             → If False at deadline (forced), send "remote start vergeten" notification.
          2. not already running
          3. available surplus >= min_surplus_w  (unless forced by deadline)
             → Multiple appliances may run simultaneously when surplus is sufficient.
        """
        if not self._day_plan:
            return

        # Track surplus already committed to appliances started this cycle
        committed_surplus_w = 0.0

        for task in self._day_plan.scheduled_tasks:
            if not (task.planned_start <= now <= task.planned_start + timedelta(minutes=2)):
                continue

            if task.appliance_type is not None:
                app_state = state.appliances.get(task.appliance_type)
                if not app_state:
                    continue

                # Guard 1: machine must be loaded and ready for remote start
                if not app_state.remote_start_allowed:
                    if task.is_forced:
                        # Deadline reached but machine not ready — notify user
                        await self._notifier.send(
                            NotificationType.APPLIANCE_REMOTE_START_REMINDER,
                            f"EnergyBrain: {task.appliance_type.value} — remote start vergeten?",
                            f"{task.appliance_type.value} stond gepland om "
                            f"{task.planned_start.strftime('%H:%M')} maar is niet gestart: "
                            f"remote start staat niet aan.\n"
                            f"Activeer remote start als het toestel klaar staat.",
                        )
                    self._log.info(
                        "appliance_skip_not_ready",
                        appliance=task.name,
                        reason="remote_start_allowed=False",
                        forced=task.is_forced,
                    )
                    continue

                # Guard 2: not already running
                if app_state.is_running:
                    continue

                # Guard 3: available surplus after already-committed appliances
                if not task.is_forced:
                    available = state.grid.surplus_w - committed_surplus_w
                    if available < task.min_surplus_w:
                        self._log.info(
                            "appliance_skip_low_surplus",
                            appliance=task.name,
                            surplus_w=round(state.grid.surplus_w),
                            committed_w=round(committed_surplus_w),
                            available_w=round(available),
                            required_w=round(task.min_surplus_w),
                        )
                        continue

                await self._start_appliance(task, state, now)
                committed_surplus_w += task.min_surplus_w

            elif task.name == "dhw_boost":
                # DHW boost: check available surplus (after other committed appliances)
                if not task.is_forced:
                    available = state.grid.surplus_w - committed_surplus_w
                    if available < task.min_surplus_w:
                        self._log.info(
                            "dhw_boost_skip_low_surplus",
                            surplus_w=round(state.grid.surplus_w),
                            available_w=round(available),
                            required_w=round(task.min_surplus_w),
                        )
                        continue
                await self._trigger_dhw_boost(state)
                committed_surplus_w += task.min_surplus_w

    async def _start_appliance(self, task, state: SystemState, now: datetime) -> None:
        from energybrain.models import Action
        action = Action(
            action_type=ActionType.START_APPLIANCE,
            target_entity=task.appliance_type.value,
            parameters={"appliance": task.appliance_type},
            reason=f"surplus_window, surplus={state.grid.surplus_w:.0f}W",
        )
        ok, reason = self._hard_limits.validate_action(action, state)
        if not ok:
            self._log.info("action_blocked_by_hard_limit", action=task.name, reason=reason)
            return
        try:
            await self._home_connect.start_appliance(task.appliance_type)
            reden = "deadline" if task.is_forced else "zonnestroom"
            self._log_executed_action(
                f"{task.appliance_type.value} gestart ({reden})", now
            )
            notif_type = NotificationType.APPLIANCE_FORCE_STARTED if task.is_forced \
                else NotificationType.APPLIANCE_STARTED
            await self._notifier.send(
                notif_type,
                f"EnergyBrain: {task.appliance_type.value} gestart",
                f"{task.appliance_type.value} gestart {'(deadline)' if task.is_forced else '(zonnestroom)'}",
            )
        except Exception as exc:
            self._log.warning("appliance_start_failed", appliance=task.appliance_type.value, error=str(exc))

    async def _trigger_dhw_boost(self, state: SystemState) -> None:
        if state.heat_pump.dhw_boost_active:
            return
        try:
            await self._ha.call_service(
                "select", "select_option",
                entity_id="select.opentherm_ssw_modus",
                option="Boost",
            )
            self._log_executed_action("DHW boost gestart (surplus)", datetime.now())
            self._log.info("dhw_boost_triggered", reason="surplus_window")
        except Exception as exc:
            self._log.warning("dhw_boost_failed", error=str(exc))

    async def _apply_temperature_control(self, state: SystemState) -> None:
        """Lower Anna setpoint to 19°C when indoor temp exceeds 22°C.

        Prevents the heat pump from heating on warm days.
        Only active when brain_mode = 'auto' and brain is enabled.
        Does NOT auto-restore — user adjusts setpoint via HA override or manually.
        """
        try:
            control = self._last_control_state
            # Skip if brain is disabled or not in auto mode
            if control is not None:
                if not getattr(control, "brain_enabled", True):
                    return
                if getattr(control, "brain_mode", "auto") != "auto":
                    return
                if getattr(control, "vacation_active", False):
                    return

            indoor = state.heat_pump.indoor_temp_c
            current_setpoint = state.heat_pump.setpoint_c

            if indoor > 22.0 and current_setpoint > 19.0:
                await self._ha.call_service(
                    "climate", "set_temperature",
                    entity_id="climate.anna",
                    temperature=19.0,
                )
                self._log_executed_action(
                    f"Anna setpoint → 19°C (kamer {indoor:.1f}°C, te warm)",
                    datetime.now(),
                )
                self._log.info(
                    "temperature_lowered_hot_day",
                    indoor_c=round(indoor, 1),
                    outdoor_c=round(state.heat_pump.outdoor_temp_c, 1),
                    setpoint_was=current_setpoint,
                )
        except Exception as exc:
            self._log.warning("temperature_control_failed", error=str(exc))

    async def _track_capacity_tariff(self, state: SystemState, now: datetime) -> None:
        """Track 15-minute grid import average for Belgian capacity tariff.

        Belgian capacity tariff = highest monthly 15-min average grid import (kW).
        Samples grid import every 60 s → averages 15 readings per quarter-hour window.
        Writes current avg and monthly peak to HA. Sends push notification when
        the current window threatens to set a new monthly peak.
        """
        try:
            import_w = max(0.0, state.grid.power_w)  # positive = importing, ignore export
            self._capacity_samples.append(import_w)

            # Keep only last 15 samples (= 15 minutes at 60 s interval)
            if len(self._capacity_samples) > 15:
                self._capacity_samples = self._capacity_samples[-15:]

            current_avg_w = sum(self._capacity_samples) / len(self._capacity_samples)
            current_avg_kw = current_avg_w / 1000.0

            # Reset monthly peak at start of new month
            month_key = now.strftime("%Y-%m")
            if self._capacity_peak_month != month_key:
                self._capacity_monthly_peak_w = 0.0
                self._capacity_peak_month = month_key

            # Update monthly peak
            if current_avg_w > self._capacity_monthly_peak_w:
                self._capacity_monthly_peak_w = current_avg_w

            peak_kw = self._capacity_monthly_peak_w / 1000.0

            # How many minutes left in this 15-min window?
            minute_in_quarter = now.minute % 15
            remaining_min = 14 - minute_in_quarter

            # Warn if current window (if sustained) would set new monthly peak
            warning = ""
            if len(self._capacity_samples) >= 8 and current_avg_w > self._capacity_monthly_peak_w * 0.9:
                warning = f" ⚠️ PIEK DREIGT"
                if current_avg_w > self._capacity_monthly_peak_w and len(self._capacity_samples) == 15:
                    # New peak confirmed — send push once per window
                    if self._capacity_window_start != now.replace(
                        minute=(now.minute // 15) * 15, second=0, microsecond=0
                    ):
                        self._capacity_window_start = now.replace(
                            minute=(now.minute // 15) * 15, second=0, microsecond=0
                        )
                        await self._notifier.send(
                            NotificationType.BATTERY_DISPATCH_STUB,
                            "EnergyBrain: Capaciteitspiek!",
                            f"Nieuwe maandpiek: {current_avg_kw:.2f} kW "
                            f"(was {peak_kw:.2f} kW). Zet nu niets extra aan.",
                        )

            current_text = (
                f"{current_avg_kw:.2f} kW gem. nu | "
                f"Maandpiek: {peak_kw:.2f} kW | "
                f"{remaining_min} min resterend in kwartier{warning}"
            )[:255]

            # Write every full minute (avoid flooding — write every 5th sample)
            if len(self._capacity_samples) % 5 == 0 or warning:
                await self._ha.call_service(
                    "input_text", "set_value",
                    entity_id="input_text.energybrain_capacity_current",
                    value=current_text,
                )
                await self._ha.call_service(
                    "input_text", "set_value",
                    entity_id="input_text.energybrain_capacity_peak",
                    value=f"Maandpiek {month_key}: {peak_kw:.2f} kW",
                )
        except Exception as exc:
            self._log.warning("capacity_tariff_tracking_failed", error=str(exc))

    async def _job_statistics(self, state: SystemState) -> None:
        """Daily statistics + financial summary. Written to HA at 22:00."""
        try:
            pv_today = state.pv.daily_energy_kwh
            import_today = state.grid.daily_import_kwh
            export_today = state.grid.daily_export_kwh

            # Self-consumption: PV energy kept in home (not exported)
            self_use_kwh = max(0.0, pv_today - export_today)
            # Financial
            import_price = self._config.static_import_price_eur_kwh
            export_price = self._config.static_export_price_eur_kwh
            savings_eur = self_use_kwh * import_price
            export_income_eur = export_today * export_price
            total_benefit_eur = savings_eur + export_income_eur

            # House consumption estimate
            battery_w = state.battery.power_w
            house_kwh_approx = max(0.0, pv_today - export_today + import_today)

            today_text = (
                f"☀️ PV: {pv_today:.1f}kWh | "
                f"📥 Import: {import_today:.1f}kWh | "
                f"📤 Export: {export_today:.1f}kWh | "
                f"🏠 Huis ~{house_kwh_approx:.1f}kWh | "
                f"🔋 SoC: {state.battery.soc_pct:.0f}%"
            )[:255]

            financial_text = (
                f"Vandaag: bespaard {savings_eur:.2f}€ (zelfverbruik {self_use_kwh:.1f}kWh) + "
                f"export {export_income_eur:.2f}€ ({export_today:.1f}kWh) = "
                f"totaal {total_benefit_eur:.2f}€ | "
                f"Tarieven: {import_price:.3f}€/kWh import, {export_price:.3f}€/kWh export"
            )[:255]

            await self._ha.call_service(
                "input_text", "set_value",
                entity_id="input_text.energybrain_stats_today",
                value=today_text,
            )
            await self._ha.call_service(
                "input_text", "set_value",
                entity_id="input_text.energybrain_stats_financial",
                value=financial_text,
            )
            self._log.info(
                "statistics_written",
                pv_kwh=round(pv_today, 2),
                self_use_kwh=round(self_use_kwh, 2),
                total_eur=round(total_benefit_eur, 2),
            )
        except Exception as exc:
            self._log.warning("statistics_job_failed", error=str(exc))

    async def _execute_action(self, action, state: SystemState) -> None:
        """Execute a validated action (used for rollbacks)."""
        from energybrain.models import ActionType as AT
        if action.action_type == AT.SET_HVAC_SETPOINT:
            setpoint = action.parameters.get("setpoint_c", 20.0)
            try:
                await self._ha.call_service(
                    "climate", "set_temperature",
                    entity_id="climate.anna",
                    temperature=setpoint,
                )
            except Exception as exc:
                self._log.warning("rollback_setpoint_failed", error=str(exc))
        elif action.action_type == AT.SET_DHW_BOOST:
            active = action.parameters.get("active", False)
            option = "Boost" if active else "Normal"
            try:
                await self._ha.call_service(
                    "select", "select_option",
                    entity_id="select.opentherm_ssw_modus",
                    option=option,
                )
            except Exception as exc:
                self._log.warning("rollback_dhw_failed", error=str(exc))

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def _startup_recovery(self) -> None:
        """Run startup checks: GoodWe → GENERAL, Marstek mode, Anna schema."""
        self._log.info("startup_recovery_begin")

        # 1. GoodWe → GENERAL
        try:
            await self._ha.call_service(
                "select", "select_option",
                entity_id="select.goodwe_bedrijfsmodus_omvormer",
                option="general",
            )
            self._log.info("startup_goodwe_set_general")
        except Exception as exc:
            self._log.warning("startup_goodwe_failed", error=str(exc))

        # 2. Marstek mode check
        try:
            raw = await self._ha.get_state("select.marstek_venuse_operating_mode")
            mode = raw["state"]
            if mode.lower() not in ("auto", "general"):
                self._log.warning("startup_marstek_mode_unexpected", mode=mode)
            else:
                self._log.info("startup_marstek_mode_ok", mode=mode)
        except Exception as exc:
            self._log.warning("startup_marstek_check_failed", error=str(exc))

        # 3. Anna schema must be 'off' (never run alongside EnergyBrain)
        try:
            raw = await self._ha.get_state("select.anna_thermostaat_schema")
            schema = raw["state"]
            if schema.lower() != "off":
                self._log.warning(
                    "startup_anna_schema_not_off",
                    schema=schema,
                    action="Please set Anna schema to 'off' in the Plugwise app.",
                )
            else:
                self._log.info("startup_anna_schema_ok")
        except Exception as exc:
            self._log.warning("startup_anna_check_failed", error=str(exc))

        self._log.info("startup_recovery_done")

    # ------------------------------------------------------------------
    # Model persistence on startup
    # ------------------------------------------------------------------

    async def _load_models(self) -> None:
        """Restore ML models from DB after startup."""
        await self._learning_store.load_thermal_model(self._thermal_model)
        await self._learning_store.load_pv_forecaster(self._pv_forecaster)
        await self._learning_store.load_pattern_learner(self._pattern_learner)
        self._log.info(
            "models_loaded",
            thermal_ready=self._thermal_model.is_ready(),
            pv_calibrated=self._pv_forecaster.is_calibrated(),
            patterns_trained=self._pattern_learner.is_trained(),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _expand_to_96(hourly) -> list[float]:
        """Expand 24 hourly PV values to 96 15-min timesteps."""
        result = []
        for h in hourly[:24]:
            result.extend([h.pv_estimated_w] * 4)
        # Pad to 96 if needed
        while len(result) < 96:
            result.append(0.0)
        return result[:96]

    def _build_week_forecast(self, state: SystemState) -> list[dict]:
        """Build 7-day forecast list from WeatherForecast hourly data."""
        if not state.weather:
            return []
        forecast = []
        hourly = state.weather.hourly
        for day_idx in range(7):
            day_hours = hourly[day_idx * 24:(day_idx + 1) * 24]
            if not day_hours:
                break
            temps = [h.temperature_c for h in day_hours]
            forecast.append({
                "day_index": day_idx,
                "avg_outdoor_c": sum(temps) / len(temps) if temps else 10.0,
                "min_outdoor_c": min(temps) if temps else 5.0,
                "daily_pv_kwh": sum(h.pv_estimated_w for h in day_hours) / 1000.0,
            })
        return forecast
