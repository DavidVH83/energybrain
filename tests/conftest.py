"""Shared pytest fixtures for EnergyBrain tests."""
import os
from datetime import datetime
from pathlib import Path

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


# ---------------------------------------------------------------------------
# Environment — minimal .env values for offline unit tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def minimal_env(monkeypatch):
    """Set mandatory env vars so load_config() works without a real .env."""
    monkeypatch.setenv("HA_URL", "http://localhost:8123")
    monkeypatch.setenv("HA_TOKEN", "test-token")
    monkeypatch.setenv("NOTIFICATION_DEVICE", "test_device")


# ---------------------------------------------------------------------------
# Model fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pv_state():
    return PVState(power_w=3500.0, daily_energy_kwh=12.5)


@pytest.fixture
def battery_state():
    return BatteryState(
        soc_pct=75.0,
        power_w=500.0,
        temperature_c=25.0,
        mode=BatteryMode.AUTO,
        write_enabled=False,
    )


@pytest.fixture
def grid_state():
    return GridState(
        power_w=-1200.0,     # -1200 W = injecting surplus
        daily_import_kwh=2.1,
        daily_export_kwh=8.4,
    )


@pytest.fixture
def heat_pump_state():
    return HeatPumpState(
        indoor_temp_c=20.5,
        outdoor_temp_c=8.0,
        setpoint_c=20.0,
        hvac_mode=HVACMode.HEAT,
        dhw_boost_active=False,
        dhw_temp_c=48.0,
    )


@pytest.fixture
def appliances():
    return {
        ApplianceType.DISHWASHER: ApplianceState(
            appliance_type=ApplianceType.DISHWASHER,
            remote_start_allowed=True,
            is_running=False,
            status=DeviceStatus.ONLINE,
        ),
        ApplianceType.WASHING_MACHINE: ApplianceState(
            appliance_type=ApplianceType.WASHING_MACHINE,
            remote_start_allowed=False,
            is_running=False,
        ),
        ApplianceType.DRYER: ApplianceState(
            appliance_type=ApplianceType.DRYER,
            remote_start_allowed=False,
            is_running=False,
        ),
    }


@pytest.fixture
def weather_forecast():
    return WeatherForecast(
        location="Korbeek-lo",
        daily_pv_kwh=18.0,
        hourly=[
            HourlyForecast(hour=h, pv_estimated_w=float(h * 200), cloud_cover_pct=10.0, temperature_c=15.0)
            for h in range(24)
        ],
    )


@pytest.fixture
def energy_price():
    return EnergyPrice(
        current_import_eur_kwh=0.25,
        current_export_eur_kwh=0.036,
        hourly_import_prices=[0.25] * 24,
        cheap_hours=[1, 2, 3, 4],
        expensive_hours=[17, 18, 19],
    )


@pytest.fixture
def system_state(pv_state, battery_state, grid_state, heat_pump_state, appliances, weather_forecast, energy_price):
    return SystemState(
        pv=pv_state,
        battery=battery_state,
        grid=grid_state,
        heat_pump=heat_pump_state,
        appliances=appliances,
        weather=weather_forecast,
        prices=energy_price,
    )


# ---------------------------------------------------------------------------
# Database fixture
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path):
    """Return an initialized in-memory-style DatabaseManager (tmp dir)."""
    from energybrain.persistence.database import DatabaseManager
    manager = DatabaseManager(tmp_path / "test.db")
    await manager.initialize()
    yield manager
    await manager.close()
