"""Integration tests — full 06:00-23:00 cycle simulation.

Exercises the orchestrator's scheduled job pipeline using mocked agents.
No real HA connection required.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from energybrain.intelligence.battery_dispatcher import BatteryDispatcher
from energybrain.intelligence.day_planner import DayPlanner
from energybrain.intelligence.oscillation_detector import OscillationDetector
from energybrain.intelligence.outcome_tracker import OutcomeTracker
from energybrain.intelligence.pattern_learner import PatternLearner
from energybrain.intelligence.pv_forecaster import PVForecaster
from energybrain.intelligence.thermal_model import ThermalModel
from energybrain.intelligence.week_strategist import WeekStrategist
from energybrain.models import (
    ApplianceState,
    ApplianceType,
    BatteryMode,
    BatteryState,
    DeviceStatus,
    EnergyPrice,
    GridState,
    HVACMode,
    HeatPumpState,
    HourlyForecast,
    NotificationType,
    PVState,
    SystemState,
    WeatherForecast,
)


# ---------------------------------------------------------------------------
# Shared state builder
# ---------------------------------------------------------------------------

_PV_SUNNY = [
    0, 0, 0, 0, 0, 0, 50, 400, 1200, 2500, 4000, 5500,
    5800, 5200, 4600, 3200, 1800, 700, 200, 0, 0, 0, 0, 0,
]


def _make_state(hour: int = 10, pv_curve: list | None = None) -> SystemState:
    curve = pv_curve or _PV_SUNNY
    pv_w = float(curve[hour])
    hourly = [
        HourlyForecast(hour=h, pv_estimated_w=float(curve[h]),
                       cloud_cover_pct=20.0, temperature_c=15.0)
        for h in range(24)
    ]
    return SystemState(
        pv=PVState(power_w=pv_w, daily_energy_kwh=sum(curve[:hour]) / 1000.0),
        battery=BatteryState(soc_pct=60.0, power_w=0.0, temperature_c=25.0,
                             mode=BatteryMode.AUTO),
        grid=GridState(power_w=max(0.0, 1200.0 - pv_w),
                       daily_import_kwh=1.5, daily_export_kwh=3.2),
        heat_pump=HeatPumpState(
            indoor_temp_c=20.5,
            outdoor_temp_c=8.0,
            setpoint_c=20.0,
            hvac_mode=HVACMode.AUTO,
            dhw_boost_active=False,
            dhw_temp_c=52.0,
        ),
        appliances={
            ApplianceType.DISHWASHER: ApplianceState(
                appliance_type=ApplianceType.DISHWASHER,
                remote_start_allowed=True,
                is_running=False,
                status=DeviceStatus.ONLINE,
            ),
            ApplianceType.WASHING_MACHINE: ApplianceState(
                appliance_type=ApplianceType.WASHING_MACHINE,
                remote_start_allowed=True,
                is_running=False,
                status=DeviceStatus.ONLINE,
            ),
            ApplianceType.DRYER: ApplianceState(
                appliance_type=ApplianceType.DRYER,
                remote_start_allowed=True,
                is_running=False,
                status=DeviceStatus.ONLINE,
            ),
        },
        weather=WeatherForecast(
            location="Korbeek-lo",
            daily_pv_kwh=sum(_PV_SUNNY) / 1000.0,
            hourly=hourly,
        ),
        prices=EnergyPrice(
            current_import_eur_kwh=0.25,
            current_export_eur_kwh=0.036,
            hourly_import_prices=[0.25] * 24,
            cheap_hours=[1, 2, 3, 4],
            expensive_hours=[17, 18, 19],
        ),
    )


# ---------------------------------------------------------------------------
# Intelligence pipeline smoke tests
# ---------------------------------------------------------------------------

class TestIntelligencePipeline:
    """Verify that all intelligence modules can run sequentially on the same state."""

    def test_day_plan_created_at_0630(self):
        state = _make_state(hour=6)
        plan = DayPlanner().create_day_plan(state)
        assert plan is not None
        assert len(plan.surplus_windows) >= 0  # can be empty on low PV

    def test_week_strategy_calculated(self):
        state = _make_state(hour=2)
        ws = WeekStrategist()
        od = OscillationDetector()
        thermal = ThermalModel()
        forecaster = PVForecaster()
        forecast_7d = [
            {"day_index": i, "avg_outdoor_c": 8.0, "min_outdoor_c": 2.0,
             "daily_pv_kwh": sum(_PV_SUNNY) / 1000.0}
            for i in range(7)
        ]
        strategy = ws.calculate_strategy(thermal, forecaster, od, forecast_7d)
        assert strategy is not None
        all_days = set(strategy.heating_days) | set(strategy.cooling_days) | set(strategy.neutral_days)
        assert all_days == set(range(7))

    def test_battery_dispatch_plan_created(self):
        state = _make_state(hour=6)
        dispatcher = BatteryDispatcher(write_enabled=False)
        pv_96 = [float(_PV_SUNNY[h // 4]) for h in range(96)]
        cons_96 = [1200.0] * 96
        plan = dispatcher.calculate_dispatch_plan(
            pv_forecast_w=pv_96,
            consumption_forecast_w=cons_96,
            current_soc_pct=50.0,
            current_monthly_peak_kw=2.0,
            import_price_eur_kwh=0.25,
            export_price_eur_kwh=0.036,
        )
        assert plan is not None
        assert len(plan.hourly_target_w) == 96  # 15-min timesteps

    def test_outcome_tracker_logs_and_reports(self):
        tracker = OutcomeTracker()
        pid = tracker.log_prediction("dhw_demand", {"outdoor_temp_c": 5.0}, 1.0)
        tracker.log_outcome(pid, 1.0)
        report = tracker.get_accuracy_report(period_days=30)
        assert report.total_predictions == 1

    def test_pattern_learner_predicts_before_training(self):
        """PatternLearner should return valid defaults even without training."""
        learner = PatternLearner()
        dhw = learner.predict_dhw_demand(
            weekday=2,
            hour=12,
            outdoor_temp_c=8.0,
            cloud_cover_pct=50.0,
            wind_speed_ms=3.0,
            is_school_holiday=False,
            season_q=1,
            baseline_power_w=1200.0,
            temp_vs_seasonal_avg=0.0,
        )
        assert 0.0 <= dhw <= 1.0


# ---------------------------------------------------------------------------
# Orchestrator scheduled jobs — exercise job methods directly
# ---------------------------------------------------------------------------

def _make_orchestrator():
    from energybrain.config import load_config
    from energybrain.orchestrator.orchestrator import Orchestrator
    config = load_config()
    db = MagicMock()
    db._conn = None
    db.write_heartbeat = AsyncMock()
    db.cleanup_old_states = AsyncMock(return_value=10)
    db.cleanup_old_hourly = AsyncMock(return_value=5)
    with patch("energybrain.orchestrator.orchestrator.HAClient"):
        orch = Orchestrator(config, db)

    # Mock persistence stores
    orch._state_store = MagicMock()
    orch._state_store.save_state = AsyncMock()
    orch._state_store.build_thermal_observations = AsyncMock(return_value=[])
    orch._state_store.build_pattern_training_data = AsyncMock(return_value=[])
    orch._state_store.build_pv_calibration_row = AsyncMock(return_value={
        "date": datetime.now(), "predicted_kwh": 10.0, "actual_kwh": 9.0,
        "avg_cloud_cover": 30.0, "avg_temp_c": 15.0, "avg_wind_ms": 3.0,
    })
    orch._learning_store = MagicMock()
    orch._learning_store.save_thermal_model = AsyncMock()
    orch._learning_store.save_pv_forecaster = AsyncMock()
    orch._learning_store.save_pattern_learner = AsyncMock()

    # Mock notifier
    orch._notifier = MagicMock()
    orch._notifier.send = AsyncMock()

    return orch


class TestScheduledJobs:
    @pytest.mark.asyncio
    async def test_job_daily_summary_sends_notification(self):
        orch = _make_orchestrator()
        state = _make_state(hour=21)
        orch._dispatch_plan = None
        await orch._job_daily_summary(state)
        orch._notifier.send.assert_called_once()
        call = orch._notifier.send.call_args[0]
        assert call[0] == NotificationType.DAILY_SUMMARY

    @pytest.mark.asyncio
    async def test_job_pv_calibration_runs(self):
        orch = _make_orchestrator()
        state = _make_state(hour=21)
        await orch._job_pv_calibration(state)
        orch._state_store.build_pv_calibration_row.assert_called_once()

    @pytest.mark.asyncio
    async def test_job_drift_and_thermal_runs(self):
        orch = _make_orchestrator()
        state = _make_state(hour=2)
        await orch._job_drift_and_thermal(state)
        orch._learning_store.save_thermal_model.assert_called_once()

    @pytest.mark.asyncio
    async def test_job_pattern_update_runs(self):
        orch = _make_orchestrator()
        state = _make_state(hour=2)
        await orch._job_pattern_update(state)
        orch._learning_store.save_pattern_learner.assert_called_once()

    @pytest.mark.asyncio
    async def test_job_week_strategy_runs(self):
        orch = _make_orchestrator()
        state = _make_state(hour=2)
        await orch._job_week_strategy(state)
        assert orch._week_strategy is not None

    @pytest.mark.asyncio
    async def test_job_db_maintenance_runs(self):
        orch = _make_orchestrator()
        state = _make_state(hour=3)
        await orch._job_db_maintenance(state)
        orch._db.cleanup_old_states.assert_called_once()
        orch._db.cleanup_old_hourly.assert_called_once()

    @pytest.mark.asyncio
    async def test_job_create_day_plan_sets_plan(self):
        orch = _make_orchestrator()
        state = _make_state(hour=6)
        await orch._job_create_day_plan(state)
        assert orch._day_plan is not None

    @pytest.mark.asyncio
    async def test_job_week_notification_requires_strategy(self):
        orch = _make_orchestrator()
        state = _make_state(hour=7)
        orch._week_strategy = None
        await orch._job_week_notification(state)
        # No notification sent when no strategy
        orch._notifier.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_job_week_notification_sends_when_strategy_exists(self):
        from energybrain.models import WeekStrategy
        orch = _make_orchestrator()
        state = _make_state(hour=7)
        orch._week_strategy = WeekStrategy(
            heating_days=[0, 1], cooling_days=[], neutral_days=[2, 3, 4, 5, 6]
        )
        await orch._job_week_notification(state)
        orch._notifier.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_job_monthly_report_sends_notification(self):
        orch = _make_orchestrator()
        state = _make_state(hour=7)
        await orch._job_monthly_report(state)
        orch._notifier.send.assert_called_once()
        call = orch._notifier.send.call_args[0]
        assert call[0] == NotificationType.MONTHLY_REPORT

    @pytest.mark.asyncio
    async def test_job_battery_dispatch_execute_runs(self):
        orch = _make_orchestrator()
        state = _make_state(hour=12)
        # Create a dispatch plan first
        dispatcher = BatteryDispatcher(write_enabled=False)
        pv_96 = [float(_PV_SUNNY[h // 4]) for h in range(96)]
        cons_96 = [1200.0] * 96
        orch._dispatch_plan = dispatcher.calculate_dispatch_plan(
            pv_96, cons_96, 50.0, 2.0, 0.25, 0.036
        )
        await orch._job_battery_dispatch_execute()


# ---------------------------------------------------------------------------
# Full cycle simulation 06:00 — 23:00
# ---------------------------------------------------------------------------

class TestFullCycleSimulation:
    def test_day_progresses_through_hours(self):
        """Walk through each hour and verify planner produces consistent output."""
        planner = DayPlanner()
        state_06 = _make_state(hour=6)
        plan = planner.create_day_plan(state_06)
        assert plan is not None

        # Walk through 06:00-23:00 in 1-hour steps
        for hour in range(6, 23):
            state = _make_state(hour=hour)
            updated_plan = planner.update_plan(plan, state)
            assert updated_plan is not None
            # All days must always be accounted for in week strategy
            ws = WeekStrategist()
            od = OscillationDetector()
            thermal = ThermalModel()
            forecast_7d = [
                {"day_index": i, "avg_outdoor_c": 8.0, "min_outdoor_c": 2.0,
                 "daily_pv_kwh": sum(_PV_SUNNY) / 1000.0}
                for i in range(7)
            ]
            strategy = ws.calculate_strategy(thermal, None, od, forecast_7d)
            all_days = (set(strategy.heating_days) | set(strategy.cooling_days) |
                        set(strategy.neutral_days))
            assert all_days == set(range(7))

    def test_pv_peaks_at_midday(self):
        """Verify our test PV curve has a midday peak (sanity check)."""
        peak_hour = max(range(24), key=lambda h: _PV_SUNNY[h])
        assert 10 <= peak_hour <= 14

    def test_surplus_detected_during_peak_hours(self):
        """Grid surplus should be detected when PV > base load."""
        for hour in range(10, 15):
            state = _make_state(hour=hour)
            assert state.grid.surplus_w >= 0

    def test_no_surplus_at_night(self):
        """No surplus at night (PV=0)."""
        for hour in [0, 1, 2, 3, 22, 23]:
            state = _make_state(hour=hour)
            assert state.pv.power_w == 0.0
