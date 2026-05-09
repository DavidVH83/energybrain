"""Tests for energybrain.persistence.database."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from energybrain.exceptions import DatabaseError
from energybrain.persistence.database import DatabaseManager


class TestDatabaseInitialization:
    async def test_initialize_creates_db_file(self, tmp_path):
        db_path = tmp_path / "sub" / "energybrain.db"
        db = DatabaseManager(db_path)
        await db.initialize()
        assert db_path.exists()
        await db.close()

    async def test_initialize_twice_is_idempotent(self, tmp_path):
        db = DatabaseManager(tmp_path / "test.db")
        await db.initialize()
        await db.close()
        db2 = DatabaseManager(tmp_path / "test.db")
        await db2.initialize()    # should not raise
        await db2.close()

    async def test_close_without_initialize_is_safe(self, tmp_path):
        db = DatabaseManager(tmp_path / "test.db")
        await db.close()          # no-op, should not raise


class TestHeartbeat:
    async def test_write_and_read_heartbeat(self, db):
        before = datetime.now()
        await db.write_heartbeat()
        ts = await db.get_last_heartbeat()
        assert ts is not None
        assert ts >= before

    async def test_get_last_heartbeat_none_when_empty(self, tmp_path):
        db = DatabaseManager(tmp_path / "empty.db")
        await db.initialize()
        ts = await db.get_last_heartbeat()
        assert ts is None
        await db.close()

    async def test_multiple_heartbeats_returns_latest(self, db):
        await db.write_heartbeat()
        await db.write_heartbeat()
        await db.write_heartbeat()
        ts = await db.get_last_heartbeat()
        assert ts is not None


class TestSystemStates:
    async def test_write_and_read_system_state(self, db):
        state = {
            "timestamp": datetime.now().isoformat(),
            "pv_power_w": 3500.0,
            "pv_daily_kwh": 12.5,
            "battery_soc_pct": 75.0,
            "battery_power_w": 500.0,
            "grid_power_w": -1200.0,
            "indoor_temp_c": 20.5,
            "outdoor_temp_c": 8.0,
            "hvac_setpoint_c": 20.0,
            "hvac_mode": "heat",
            "hvac_regime": "heating",
            "dhw_boost_active": 0,
            "dhw_temp_c": 48.0,
            "baseline_power_w": 400.0,
            "occupancy_type": "normal",
            "dishwasher_running": 0,
            "washing_machine_running": 0,
            "dryer_running": 0,
        }
        await db.write_system_state(state)
        row = await db._fetchone("SELECT * FROM system_states ORDER BY id DESC LIMIT 1")
        assert row is not None
        assert row["pv_power_w"] == pytest.approx(3500.0)

    async def test_cleanup_removes_old_states(self, db):
        old_ts = (datetime.now() - timedelta(days=100)).isoformat()
        await db.write_system_state({
            "timestamp": old_ts,
            "pv_power_w": 0.0,
        })
        new_ts = datetime.now().isoformat()
        await db.write_system_state({
            "timestamp": new_ts,
            "pv_power_w": 100.0,
        })
        deleted = await db.cleanup_old_states(retention_days=90)
        assert deleted == 1
        row = await db._fetchone("SELECT COUNT(*) AS cnt FROM system_states")
        assert row["cnt"] == 1


class TestPredictions:
    async def test_log_prediction(self, db):
        await db.log_prediction(
            prediction_id="uuid-001",
            model_name="dhw_demand",
            features={"outdoor_temp_c": 5.0, "hour": 7},
            predicted_value=0.85,
        )
        row = await db._fetchone(
            "SELECT * FROM predictions WHERE prediction_id=?", ("uuid-001",)
        )
        assert row is not None
        assert row["model_name"] == "dhw_demand"
        assert row["actual_value"] is None

    async def test_log_outcome_fills_in_actuals(self, db):
        await db.log_prediction("uuid-002", "pv_forecast", {}, 5000.0)
        await db.log_outcome("uuid-002", 4800.0, is_correct=True)
        row = await db._fetchone(
            "SELECT * FROM predictions WHERE prediction_id=?", ("uuid-002",)
        )
        assert row["actual_value"] == pytest.approx(4800.0)
        assert row["is_correct"] == 1

    async def test_log_outcome_incorrect(self, db):
        await db.log_prediction("uuid-003", "cooking_peak", {"weekday": 2}, 0.9)
        await db.log_outcome("uuid-003", 0.0, is_correct=False)
        row = await db._fetchone(
            "SELECT * FROM predictions WHERE prediction_id=?", ("uuid-003",)
        )
        assert row["is_correct"] == 0

    async def test_duplicate_prediction_id_ignored(self, db):
        await db.log_prediction("dup-001", "dhw_demand", {}, 0.5)
        await db.log_prediction("dup-001", "dhw_demand", {}, 0.9)  # should be ignored
        row = await db._fetchone(
            "SELECT COUNT(*) AS cnt FROM predictions WHERE prediction_id='dup-001'"
        )
        assert row["cnt"] == 1


class TestAccuracyReports:
    async def test_save_accuracy_report(self, db):
        import json
        report = {
            "period_start": "2026-04-01",
            "period_end": "2026-04-30",
            "dhw_accuracy_pct": 82.0,
            "appliance_loading_accuracy_pct": 78.0,
            "pv_forecast_accuracy_pct": 91.0,
            "cooking_peak_accuracy_pct": 75.0,
            "drift_detected": json.dumps({"dhw_demand": False}),
            "total_predictions": 1440,
            "estimated_savings_eur": 12.50,
            "feature_importances": json.dumps({}),
            "generated_at": datetime.now().isoformat(),
            "notification_sent": 0,
        }
        await db.save_accuracy_report(report)
        row = await db._fetchone("SELECT * FROM accuracy_reports ORDER BY id DESC LIMIT 1")
        assert row is not None
        assert row["dhw_accuracy_pct"] == pytest.approx(82.0)


class TestSafetyEvents:
    async def test_log_safety_event(self, db):
        await db.log_safety_event(
            event_type="indoor_temp_min_winter",
            severity="WARNING",
            message="Indoor temp 16.8°C below 17.0°C threshold",
            action_taken="force_heat",
            notification_sent=True,
        )
        row = await db._fetchone(
            "SELECT * FROM safety_events ORDER BY id DESC LIMIT 1"
        )
        assert row is not None
        assert row["event_type"] == "indoor_temp_min_winter"
        assert row["notification_sent"] == 1

    async def test_multiple_safety_events(self, db):
        for i in range(3):
            await db.log_safety_event(f"type_{i}", "INFO", f"msg {i}")
        row = await db._fetchone("SELECT COUNT(*) AS cnt FROM safety_events")
        assert row["cnt"] == 3


class TestHourlyAggregates:
    async def test_aggregate_to_hourly_from_states(self, db):
        target_date = datetime(2026, 4, 1)
        for h in range(3):
            for m in [0, 30]:
                ts = target_date.replace(hour=h, minute=m).isoformat()
                await db.write_system_state({
                    "timestamp": ts,
                    "pv_power_w": 1000.0 + h * 100,
                    "grid_power_w": -500.0,
                    "indoor_temp_c": 20.0,
                    "outdoor_temp_c": 10.0,
                    "baseline_power_w": 300.0,
                    "hvac_mode": "heat",
                    "dhw_boost_active": 0,
                    "occupancy_type": "normal",
                })
        hours = await db.aggregate_to_hourly(target_date)
        assert hours == 3

    async def test_cleanup_hourly_aggregates(self, db):
        old_hour = (datetime.now() - timedelta(days=3 * 365)).isoformat()
        await db.write_hourly_aggregate({"hour_start": old_hour, "avg_pv_power_w": 0.0})
        deleted = await db.cleanup_old_hourly(retention_years=2)
        assert deleted >= 1


class TestDatabaseErrors:
    async def test_execute_without_initialize_raises(self, tmp_path):
        db = DatabaseManager(tmp_path / "uninit.db")
        with pytest.raises(DatabaseError, match="not initialized"):
            await db._execute("SELECT 1")

    async def test_fetchone_without_initialize_raises(self, tmp_path):
        db = DatabaseManager(tmp_path / "uninit.db")
        with pytest.raises(DatabaseError, match="not initialized"):
            await db._fetchone("SELECT 1")
