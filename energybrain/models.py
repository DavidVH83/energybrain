"""Core data models. All modules communicate exclusively via these models."""
from dataclasses import dataclass, field
from datetime import datetime, time
from enum import Enum
from typing import Optional


class DeviceStatus(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"
    UNKNOWN = "unknown"

class HVACMode(Enum):
    HEAT = "heat"
    COOL = "cool"
    OFF = "off"
    AUTO = "auto"

class BatteryMode(Enum):
    AUTO = "Auto"
    AI = "AI"
    MANUAL = "Manual"
    PASSIVE = "Passive"

class ApplianceType(Enum):
    DISHWASHER = "dishwasher"
    WASHING_MACHINE = "washing_machine"
    DRYER = "dryer"

class ActionType(Enum):
    SET_DHW_BOOST = "set_dhw_boost"
    SET_HVAC_SETPOINT = "set_hvac_setpoint"
    SET_HVAC_MODE = "set_hvac_mode"
    START_APPLIANCE = "start_appliance"
    STOP_APPLIANCE = "stop_appliance"
    SET_BATTERY_MODE = "set_battery_mode"      # STUB if V153
    SET_BATTERY_POWER = "set_battery_power"     # STUB if V153
    SEND_NOTIFICATION = "send_notification"
    NO_ACTION = "no_action"

class NotificationType(Enum):
    SOLAR_OPPORTUNITY = "solar_opportunity"
    APPLIANCE_STARTED = "appliance_started"
    APPLIANCE_FORCE_STARTED = "force_started"
    APPLIANCE_REMOTE_START_REMINDER = "remote_start_reminder"  # machine klaar maar remote start vergeten
    DHW_BOOST = "dhw_boost"
    SAFETY_ALARM = "safety_alarm"              # Always send, no throttle
    DAILY_SUMMARY = "daily_summary"
    WEEK_STRATEGY = "week_strategy"
    MODEL_DRIFT = "model_drift"
    MONTHLY_REPORT = "monthly_report"
    BATTERY_DISPATCH_STUB = "battery_dispatch"


@dataclass
class PVState:
    power_w: float
    daily_energy_kwh: float
    status: DeviceStatus = DeviceStatus.UNKNOWN
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class BatteryState:
    soc_pct: float
    power_w: float                  # Positive = charging, negative = discharging
    temperature_c: float
    mode: BatteryMode = BatteryMode.AUTO
    write_enabled: bool = False     # False = V153 stub active
    status: DeviceStatus = DeviceStatus.UNKNOWN
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class GridState:
    power_w: float                  # Positive = consuming, negative = injecting
    daily_import_kwh: float
    daily_export_kwh: float
    status: DeviceStatus = DeviceStatus.UNKNOWN
    timestamp: datetime = field(default_factory=datetime.now)

    @property
    def surplus_w(self) -> float:
        """Positive = surplus going to grid."""
        return max(0.0, -self.power_w)


@dataclass
class HeatPumpState:
    indoor_temp_c: float
    outdoor_temp_c: float
    setpoint_c: float
    hvac_mode: HVACMode
    dhw_boost_active: bool
    dhw_temp_c: Optional[float] = None
    status: DeviceStatus = DeviceStatus.UNKNOWN
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ApplianceState:
    appliance_type: ApplianceType
    remote_start_allowed: bool
    is_running: bool
    program: Optional[str] = None
    remaining_seconds: Optional[int] = None
    waiting_since: Optional[datetime] = None
    status: DeviceStatus = DeviceStatus.UNKNOWN
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class HourlyForecast:
    """PV and weather forecast for a single hour."""
    hour: int                       # 0-23
    pv_estimated_w: float
    cloud_cover_pct: float
    temperature_c: float
    is_surplus_window: bool = False


@dataclass
class WeatherForecast:
    location: str
    daily_pv_kwh: float
    hourly: list[HourlyForecast]    # 168 hours = 7 days
    pv_calibration_factor: float = 1.0
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class EnergyPrice:
    current_import_eur_kwh: float
    current_export_eur_kwh: float
    hourly_import_prices: list[float]
    cheap_hours: list[int]
    expensive_hours: list[int]
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ThermalModelParams:
    """Learned thermal parameters of the house."""
    cooling_rate_c_per_hour: float = 0.3
    heating_rate_c_per_hour: float = 0.15
    thermal_mass_hours: float = 8.0
    r2_score: float = 0.0
    samples_count: int = 0
    is_trained: bool = False
    model_type: str = "linear"      # "linear" | "gradient_boosting"
    last_updated: datetime = field(default_factory=datetime.now)


@dataclass
class PredictionRecord:
    """Single prediction logged by OutcomeTracker for feedback loop."""
    prediction_id: str
    model_name: str
    features: dict
    predicted_value: float
    actual_value: Optional[float] = None
    predicted_at: datetime = field(default_factory=datetime.now)
    outcome_at: Optional[datetime] = None
    is_correct: Optional[bool] = None


@dataclass
class AccuracyReport:
    """Monthly accuracy self-report across all models."""
    period_start: datetime
    period_end: datetime
    dhw_accuracy_pct: float
    appliance_loading_accuracy_pct: float
    pv_forecast_accuracy_pct: float
    cooking_peak_accuracy_pct: float
    drift_detected: dict[str, bool]
    total_predictions: int
    estimated_savings_eur: float
    generated_at: datetime = field(default_factory=datetime.now)


@dataclass
class BatteryDispatchPlan:
    """
    24-hour battery charge/discharge plan from BatteryDispatcher (MPC).

    STUB until Marstek V154 firmware fixes RS485 write regression (broken in V153).
    RS485 adapter (Waveshare) already installed — only firmware fix needed.
    """
    date: datetime
    hourly_target_w: list[float]
    expected_savings_eur: float
    peak_prevention_kw: float
    is_stub: bool = True
    generated_at: datetime = field(default_factory=datetime.now)


@dataclass
class SurplusWindow:
    """A predicted continuous time window with solar surplus."""
    start_hour: int
    end_hour: int
    avg_surplus_w: float
    total_energy_kwh: float

    @property
    def duration_hours(self) -> float:
        return self.end_hour - self.start_hour


@dataclass
class ScheduledTask:
    """A task scheduled by DayPlanner."""
    name: str
    appliance_type: Optional[ApplianceType]
    planned_start: datetime
    min_surplus_w: float
    estimated_duration_hours: float
    hard_deadline: time
    max_wait_hours: float
    priority: int
    is_forced: bool = False


@dataclass
class DayPlan:
    """Full day plan. Created at 06:30, updated every 15 min."""
    date: datetime
    total_pv_forecast_kwh: float
    surplus_windows: list[SurplusWindow]
    scheduled_tasks: list[ScheduledTask]
    week_strategy_note: str = ""
    generated_at: datetime = field(default_factory=datetime.now)


@dataclass
class WeekStrategy:
    """7-day thermal strategy. Recalculated nightly at 02:00."""
    heating_days: list[int]         # Day indices (0=Mon)
    cooling_days: list[int]
    neutral_days: list[int]
    oscillation_risk: bool = False
    reasoning: str = ""
    generated_at: datetime = field(default_factory=datetime.now)


@dataclass
class SystemState:
    """Unified system state — single source of truth."""
    pv: PVState
    battery: BatteryState
    grid: GridState
    heat_pump: HeatPumpState
    appliances: dict[ApplianceType, ApplianceState]
    weather: WeatherForecast
    prices: EnergyPrice
    day_plan: Optional[DayPlan] = None
    week_strategy: Optional[WeekStrategy] = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Action:
    action_type: ActionType
    target_entity: str
    parameters: dict = field(default_factory=dict)
    priority: int = 0
    reason: str = ""
    is_stub: bool = False
    rollback_after_minutes: Optional[int] = None


@dataclass
class ActionResult:
    success: bool
    action: Action
    executed_at: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None
    is_stub: bool = False


@dataclass
class ControlState:
    """Control state from HA input helpers — read by DayPlanner every cycle."""
    brain_enabled: bool = True
    brain_mode: str = "auto"
    vacation_active: bool = False
    vacation_start: Optional[datetime] = None
    vacation_end: Optional[datetime] = None
    dhw_boost_now: bool = False
    dhw_target_temp: float = 55.0
    timestamp: datetime = field(default_factory=datetime.now)
