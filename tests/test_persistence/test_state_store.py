"""Tests for energybrain.persistence.state_store."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    PVState,
    SystemState,
    WeatherForecast,
)
from energybrain.persistence.state_store import StateStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_system_state(
    pv_w: float = 2000.0,
    grid_w: float = -500.0,
    indoor_c: float = 20.0,
    outdoor_c: float = 10.0,
    hvac_mode: HVACMode = HVACMode.HEAT,
    dhw_boost: bool = False,
    dishwasher_running: bool = False,
    washing_running: bool = False,
    dryer_running: bool = False,
) -> SystemState:
    return SystemState(
        pv=PVState(power_w=pv_w, daily_energy_kwh=5.0),
        battery=BatteryState(soc_pct=80.0, power_w=0.0, temperature_c=25.0),
        grid=GridState(power_w=grid_w, daily_import_kwh=1.0, daily_export_kwh=3.0),
        heat_pump=HeatPumpState(
            indoor_temp_c=indoor_c,
            outdoor_temp_c=outdoor_c,
            setpoint_c=20.0,
            hvac_mode=hvac_mode,
            dhw_boost_active=dhw_boost,
            dhw_temp_c=50.0,
        ),
        appliances={
            ApplianceType.DISHWASHER: ApplianceState(
                appliance_type=ApplianceType.DISHWASHER,
                remote_start_allowed=True,
                is_running=dishwasher_running,
            ),
            ApplianceType.WASHING_MACHINE: ApplianceState(
                appliance_type=ApplianceType.WASHING_MACHINE,
                remote_start_allowed=False,
                is_running=washing_running,
            ),
            ApplianceType.DRYER: ApplianceState(
                appliance_type=ApplianceType.DRYER,
                remote_start_allowed=False,
                is_running=dryer_running,
            ),
        },
        weather=WeatherForecast(
            location="Test",
            daily_pv_kwh=10.0,
            hourly=[HourlyForecast(hour=h, pv_estimated_w=0.0, cloud_cover_pct=50.0, temperature_c=10.0) for h in range(24)],
        ),
        prices=EnergyPrice(
            current_import_eur_kwh=0.25,
            current_export_eur_kwh=0.036,
            hourly_import_prices=[0.25] * 24,
            cheap_hours=[],
            expensive_hours=[],
        ),
    )


def _make_db_row(
    timestamp: str,
    indoor_c: float = 20.0,
    outdoor_c: float = 10.0,
    pv_w: float = 0.0,
    hvac_mode: str = "heat",
    dhw_boost: int = 0,
    baseline_w: float = 1000.0,
    dishwasher: int = 0,
    washing: int = 0,
    dryer: int = 0,
) -> dict:
    return {
        "id": 1,
        "timestamp": timestamp,
        "indoor_temp_c": indoor_c,
        "outdoor_temp_c": outdoor_c,
        "pv_power_w": pv_w,
        "hvac_mode": hvac_mode,
        "dhw_boost_active": dhw_boost,
        "baseline_power_w": baseline_w,
        "dishwasher_running": dishwasher,
        "washing_machine_running": washing,
        "dryer_running": dryer,
    }


# ---------------------------------------------------------------------------
# save_state
# ---------------------------------------------------------------------------

class TestSaveState:
    @pytest.mark.asyncio
    async def test_save_state_calls_write_system_state(self):
        db = MagicMock()
        db.write_system_state = AsyncMock()
        store = StateStore(db)
        state = _make_system_state()
        await store.save_state(state)
        db.write_system_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_save_state_dishwasher_running_encoded(self):
        db = MagicMock()
        captured = {}

        async def capture(row):
            captured.update(row)

        db.write_system_state = capture
        store = StateStore(db)
        state = _make_system_state(dishwasher_running=True)
        await store.save_state(state)
        assert captured["dishwasher_running"] == 1

    @pytest.mark.asyncio
    async def test_save_state_hvac_regime_heating(self):
        db = MagicMock()
        captured = {}

        async def capture(row):
            captured.update(row)

        db.write_system_state = capture
        store = StateStore(db)
        state = _make_system_state(hvac_mode=HVACMode.HEAT)
        await store.save_state(state)
        assert captured["hvac_regime"] == "heating"

    @pytest.mark.asyncio
    async def test_save_state_hvac_regime_idle(self):
        db = MagicMock()
        captured = {}

        async def capture(row):
            captured.update(row)

        db.write_system_state = capture
        store = StateStore(db)
        state = _make_system_state(hvac_mode=HVACMode.AUTO)
        await store.save_state(state)
        assert captured["hvac_regime"] == "idle"


# ---------------------------------------------------------------------------
# build_thermal_observations
# ---------------------------------------------------------------------------

class TestBuildThermalObservations:
    @pytest.mark.asyncio
    async def test_empty_when_no_db_connection(self):
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        result = await store.build_thermal_observations()
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_rows_too_far_apart(self):
        now = datetime.now()
        rows = [
            _make_db_row((now - timedelta(minutes=10)).isoformat(), indoor_c=20.0),
            _make_db_row(now.isoformat(), indoor_c=21.0),
        ]
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states = AsyncMock(return_value=rows)
        result = await store.build_thermal_observations()
        assert result == []

    @pytest.mark.asyncio
    async def test_produces_observation_for_close_rows(self):
        now = datetime.now()
        rows = [
            _make_db_row((now - timedelta(seconds=60)).isoformat(), indoor_c=20.0, outdoor_c=5.0, pv_w=1000.0),
            _make_db_row(now.isoformat(), indoor_c=20.1, outdoor_c=5.0, pv_w=1000.0),
        ]
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states = AsyncMock(return_value=rows)
        result = await store.build_thermal_observations()
        assert len(result) == 1
        assert result[0]["outdoor_temp_c"] == 5.0
        assert "delta_indoor_c_per_hour" in result[0]

    @pytest.mark.asyncio
    async def test_delta_calculation_correct(self):
        now = datetime.now()
        # 60-second gap, 1°C rise → delta = 1 / (1/60) = 60 °C/h
        rows = [
            _make_db_row((now - timedelta(seconds=60)).isoformat(), indoor_c=20.0),
            _make_db_row(now.isoformat(), indoor_c=21.0),
        ]
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states = AsyncMock(return_value=rows)
        result = await store.build_thermal_observations()
        assert abs(result[0]["delta_indoor_c_per_hour"] - 60.0) < 0.1

    @pytest.mark.asyncio
    async def test_skips_rows_too_close_together(self):
        now = datetime.now()
        rows = [
            _make_db_row((now - timedelta(seconds=10)).isoformat(), indoor_c=20.0),
            _make_db_row(now.isoformat(), indoor_c=20.1),
        ]
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states = AsyncMock(return_value=rows)
        result = await store.build_thermal_observations()
        assert result == []


# ---------------------------------------------------------------------------
# build_pattern_training_data
# ---------------------------------------------------------------------------

class TestBuildPatternTrainingData:
    @pytest.mark.asyncio
    async def test_empty_when_no_rows(self):
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states = AsyncMock(return_value=[])
        result = await store.build_pattern_training_data()
        assert result == []

    @pytest.mark.asyncio
    async def test_groups_by_date(self):
        base = "2025-01-15"
        rows = [
            _make_db_row(f"{base}T08:00:00"),
            _make_db_row(f"{base}T12:00:00"),
            _make_db_row("2025-01-16T10:00:00"),
        ]
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states = AsyncMock(return_value=rows)
        result = await store.build_pattern_training_data()
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_dhw_label_set_when_boost_active(self):
        rows = [_make_db_row("2025-01-15T10:00:00", dhw_boost=1)]
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states = AsyncMock(return_value=rows)
        result = await store.build_pattern_training_data()
        assert result[0]["dhw_needed"] == 1

    @pytest.mark.asyncio
    async def test_dishwasher_label_set(self):
        rows = [_make_db_row("2025-01-15T10:00:00", dishwasher=1)]
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states = AsyncMock(return_value=rows)
        result = await store.build_pattern_training_data()
        assert result[0]["dishwasher_loaded"] == 1

    @pytest.mark.asyncio
    async def test_cooking_peak_hour_in_range(self):
        rows = [
            _make_db_row("2025-01-15T17:00:00", baseline_w=3000.0),
            _make_db_row("2025-01-15T18:00:00", baseline_w=2000.0),
        ]
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states = AsyncMock(return_value=rows)
        result = await store.build_pattern_training_data()
        assert result[0]["cooking_peak_hour"] == 17.0

    @pytest.mark.asyncio
    async def test_cooking_peak_defaults_when_no_evening_rows(self):
        rows = [_make_db_row("2025-01-15T10:00:00", baseline_w=1000.0)]
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states = AsyncMock(return_value=rows)
        result = await store.build_pattern_training_data()
        assert result[0]["cooking_peak_hour"] == 18.0

    @pytest.mark.asyncio
    async def test_training_row_contains_expected_keys(self):
        rows = [_make_db_row("2025-01-15T10:00:00")]
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states = AsyncMock(return_value=rows)
        result = await store.build_pattern_training_data()
        row = result[0]
        for key in ("weekday", "hour", "outdoor_temp_c", "dhw_needed", "dishwasher_loaded",
                    "washing_loaded", "dryer_loaded", "cooking_peak_hour"):
            assert key in row


# ---------------------------------------------------------------------------
# build_pv_calibration_row
# ---------------------------------------------------------------------------

class TestBuildPvCalibrationRow:
    @pytest.mark.asyncio
    async def test_returns_dict_with_correct_keys(self):
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states_for_date = AsyncMock(return_value=[])
        date = datetime(2025, 6, 1)
        result = await store.build_pv_calibration_row(date, 12.0, 10.5)
        assert result["predicted_kwh"] == 12.0
        assert result["actual_kwh"] == 10.5

    @pytest.mark.asyncio
    async def test_avg_temp_computed_from_rows(self):
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        rows = [
            {"outdoor_temp_c": 10.0},
            {"outdoor_temp_c": 20.0},
        ]
        store._fetch_system_states_for_date = AsyncMock(return_value=rows)
        date = datetime(2025, 6, 1)
        result = await store.build_pv_calibration_row(date, 10.0, 9.0)
        assert result["avg_temp_c"] == pytest.approx(15.0)

    @pytest.mark.asyncio
    async def test_avg_temp_defaults_when_no_rows(self):
        db = MagicMock()
        db._conn = None
        store = StateStore(db)
        store._fetch_system_states_for_date = AsyncMock(return_value=[])
        date = datetime(2025, 6, 1)
        result = await store.build_pv_calibration_row(date, 10.0, 9.0)
        assert result["avg_temp_c"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# _hvac_regime static method
# ---------------------------------------------------------------------------

class TestHvacRegime:
    def test_heating_mode(self):
        state = _make_system_state(hvac_mode=HVACMode.HEAT)
        assert StateStore._hvac_regime(state) == "heating"

    def test_cooling_mode(self):
        state = _make_system_state(hvac_mode=HVACMode.COOL)
        assert StateStore._hvac_regime(state) == "cooling"

    def test_auto_mode_is_idle(self):
        state = _make_system_state(hvac_mode=HVACMode.AUTO)
        assert StateStore._hvac_regime(state) == "idle"
