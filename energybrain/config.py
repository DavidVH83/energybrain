"""Configuration loader. All settings come from environment variables via .env."""
import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path

from dotenv import load_dotenv

from energybrain.exceptions import ConfigError

load_dotenv()


def _required(name: str) -> str:
    """Return a required env var or raise ConfigError."""
    value = os.environ.get(name)
    if not value:
        raise ConfigError(f"Required environment variable {name!r} is not set")
    return value


def _get(name: str, default: str) -> str:
    return os.environ.get(name, default)


def _get_float(name: str, default: float) -> float:
    return float(os.environ.get(name, str(default)))


def _get_int(name: str, default: int) -> int:
    return int(os.environ.get(name, str(default)))


def _get_bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).lower() in ("true", "1", "yes")


def _parse_time(name: str, default: str) -> time:
    val = os.environ.get(name, default)
    h, m = val.split(":")
    return time(int(h), int(m))


@dataclass(frozen=True)
class Config:
    """Immutable runtime configuration loaded from environment variables."""

    # Home Assistant
    ha_url: str
    ha_token: str
    notification_device: str

    # Marstek write guard
    marstek_write_enabled: bool

    # Energy pricing
    entsoe_api_key: str
    static_import_price_eur_kwh: float
    static_export_price_eur_kwh: float
    capacity_tariff_eur_kw_year: float

    # Location
    latitude: float
    longitude: float
    timezone: str

    # Surplus thresholds (W)
    surplus_dhw_w: float
    surplus_dishwasher_w: float
    surplus_washing_machine_w: float
    surplus_dryer_w: float
    surplus_battery_w: float
    surplus_hvac_w: float

    # Battery safety
    battery_soc_min_pct: float
    battery_soc_dhw_min_pct: float
    battery_soc_appliance_min_pct: float

    # HVAC safety
    hvac_max_setpoint_c: float
    hvac_min_setpoint_c: float
    hvac_max_step_per_cycle_c: float
    hvac_frost_outdoor_c: float
    indoor_temp_min_winter_c: float

    # Appliance deadlines
    dishwasher_max_wait_h: float
    dishwasher_hard_deadline: time
    washing_machine_max_wait_h: float
    washing_machine_hard_deadline: time
    dryer_max_wait_h: float
    dryer_hard_deadline: time

    # Intelligence thresholds
    thermal_model_min_samples: int
    pattern_learner_min_days_basic: int
    pattern_learner_min_days_seasonal: int
    pattern_learner_min_days_yearly: int
    pv_calibration_min_days: int
    oscillation_switch_threshold: int
    thermal_r2_upgrade_threshold: float

    # OutcomeTracker
    drift_threshold_pct: float
    drift_window_days: int
    accuracy_baseline_days: int

    # BatteryDispatcher MPC
    battery_mpc_horizon_hours: int
    battery_mpc_timestep_min: int
    battery_max_soc_pct: float
    battery_dispatch_log_hourly: bool

    # System
    cycle_interval_s: int
    watchdog_interval_s: int
    log_level: str
    db_path: Path
    db_retention_days: int
    db_hourly_retention_years: int

    # Contract
    contract_type: str
    cheap_hour_threshold_pct: float
    expensive_hour_threshold_pct: float

    # Cooking peak window
    cooking_peak_start_default: time
    cooking_peak_end_default: time
    min_gap_between_starts_min: int

    # Start verification
    start_verify_delay_s: int
    start_retry_delay_s: int

    # DHW
    dhw_target_temp_c: float


def load_config() -> Config:
    """Load and validate configuration from environment variables.

    Returns:
        Populated Config instance.

    Raises:
        ConfigError: If a required variable is missing.
        ValueError: If a numeric variable cannot be parsed.
    """
    return Config(
        ha_url=_required("HA_URL"),
        ha_token=_required("HA_TOKEN"),
        notification_device=_required("NOTIFICATION_DEVICE"),
        marstek_write_enabled=_get_bool("MARSTEK_WRITE_ENABLED", False),
        entsoe_api_key=_get("ENTSOE_API_KEY", ""),
        static_import_price_eur_kwh=_get_float("STATIC_IMPORT_PRICE_EUR_KWH", 0.25),
        static_export_price_eur_kwh=_get_float("STATIC_EXPORT_PRICE_EUR_KWH", 0.036),
        capacity_tariff_eur_kw_year=_get_float("CAPACITY_TARIFF_EUR_KW_YEAR", 47.50),
        latitude=_get_float("LATITUDE", 50.8597),
        longitude=_get_float("LONGITUDE", 4.7628),
        timezone=_get("TIMEZONE", "Europe/Brussels"),
        surplus_dhw_w=_get_float("SURPLUS_DHW_W", 2000),
        surplus_dishwasher_w=_get_float("SURPLUS_DISHWASHER_W", 1800),
        surplus_washing_machine_w=_get_float("SURPLUS_WASHING_MACHINE_W", 2000),
        surplus_dryer_w=_get_float("SURPLUS_DRYER_W", 2500),
        surplus_battery_w=_get_float("SURPLUS_BATTERY_W", 500),
        surplus_hvac_w=_get_float("SURPLUS_HVAC_W", 1500),
        battery_soc_min_pct=_get_float("BATTERY_SOC_MIN_PCT", 10),
        battery_soc_dhw_min_pct=_get_float("BATTERY_SOC_DHW_MIN_PCT", 50),
        battery_soc_appliance_min_pct=_get_float("BATTERY_SOC_APPLIANCE_MIN_PCT", 70),
        hvac_max_setpoint_c=_get_float("HVAC_MAX_SETPOINT_C", 22.5),
        hvac_min_setpoint_c=_get_float("HVAC_MIN_SETPOINT_C", 16.0),
        hvac_max_step_per_cycle_c=_get_float("HVAC_MAX_STEP_PER_CYCLE_C", 0.5),
        hvac_frost_outdoor_c=_get_float("HVAC_FROST_OUTDOOR_C", -2.0),
        indoor_temp_min_winter_c=_get_float("INDOOR_TEMP_MIN_WINTER_C", 17.0),
        dishwasher_max_wait_h=_get_float("DISHWASHER_MAX_WAIT_H", 4),
        dishwasher_hard_deadline=_parse_time("DISHWASHER_HARD_DEADLINE", "20:00"),
        washing_machine_max_wait_h=_get_float("WASHING_MACHINE_MAX_WAIT_H", 6),
        washing_machine_hard_deadline=_parse_time("WASHING_MACHINE_HARD_DEADLINE", "20:00"),
        dryer_max_wait_h=_get_float("DRYER_MAX_WAIT_H", 8),
        dryer_hard_deadline=_parse_time("DRYER_HARD_DEADLINE", "21:00"),
        thermal_model_min_samples=_get_int("THERMAL_MODEL_MIN_SAMPLES", 336),
        pattern_learner_min_days_basic=_get_int("PATTERN_LEARNER_MIN_DAYS_BASIC", 14),
        pattern_learner_min_days_seasonal=_get_int("PATTERN_LEARNER_MIN_DAYS_SEASONAL", 90),
        pattern_learner_min_days_yearly=_get_int("PATTERN_LEARNER_MIN_DAYS_YEARLY", 365),
        pv_calibration_min_days=_get_int("PV_CALIBRATION_MIN_DAYS", 30),
        oscillation_switch_threshold=_get_int("OSCILLATION_SWITCH_THRESHOLD", 3),
        thermal_r2_upgrade_threshold=_get_float("THERMAL_R2_UPGRADE_THRESHOLD", 0.85),
        drift_threshold_pct=_get_float("DRIFT_THRESHOLD_PCT", 15.0),
        drift_window_days=_get_int("DRIFT_WINDOW_DAYS", 14),
        accuracy_baseline_days=_get_int("ACCURACY_BASELINE_DAYS", 30),
        battery_mpc_horizon_hours=_get_int("BATTERY_MPC_HORIZON_HOURS", 24),
        battery_mpc_timestep_min=_get_int("BATTERY_MPC_TIMESTEP_MIN", 15),
        battery_max_soc_pct=_get_float("BATTERY_MAX_SOC_PCT", 95),
        battery_dispatch_log_hourly=_get_bool("BATTERY_DISPATCH_LOG_HOURLY", True),
        cycle_interval_s=_get_int("CYCLE_INTERVAL_S", 60),
        watchdog_interval_s=_get_int("WATCHDOG_INTERVAL_S", 300),
        log_level=_get("LOG_LEVEL", "INFO"),
        db_path=Path(_get("DB_PATH", "energybrain.db")),
        db_retention_days=_get_int("DB_RETENTION_DAYS", 90),
        db_hourly_retention_years=_get_int("DB_HOURLY_RETENTION_YEARS", 2),
        contract_type=_get("CONTRACT_TYPE", "static"),
        cheap_hour_threshold_pct=_get_float("CHEAP_HOUR_THRESHOLD_PCT", 70),
        expensive_hour_threshold_pct=_get_float("EXPENSIVE_HOUR_THRESHOLD_PCT", 130),
        cooking_peak_start_default=_parse_time("COOKING_PEAK_START_DEFAULT", "17:00"),
        cooking_peak_end_default=_parse_time("COOKING_PEAK_END_DEFAULT", "18:30"),
        min_gap_between_starts_min=_get_int("MIN_GAP_BETWEEN_STARTS_MIN", 15),
        start_verify_delay_s=_get_int("START_VERIFY_DELAY_S", 60),
        start_retry_delay_s=_get_int("START_RETRY_DELAY_S", 120),
        dhw_target_temp_c=_get_float("DHW_TARGET_TEMP_C", 55.0),
    )
