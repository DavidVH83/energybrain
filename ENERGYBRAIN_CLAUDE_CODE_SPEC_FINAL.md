# EnergyBrain — Claude Code Specification v2.0
<!-- Herschreven: 2026-05-04 | Echte intelligentie, niet alleen automations in Python -->

## Opdracht aan Claude Code

Bouw EnergyBrain fase per fase volgens deze spec.
- Schrijf alle code en commentaar in **Engels**
- Schrijf alle docstrings in **Google-stijl Engels**
- Na elke fase: run de testen en rapporteer voor je verder gaat
- Nooit hardcoded IPs, tokens of wachtwoorden — alles via `.env`
- Dit wordt open source — schrijf code alsof anderen het beoordelen

---

## 0. Wat dit systeem IS en wat het NIET is

### Wat het IS
Een **vooruitdenkend, lerend** energiebeheersysteem met drie tijdshorizonten:
- **Realtime (60s):** reageer op acute veranderingen
- **Dagplan (15min):** plan de dag op basis van PV-verwachting
- **Weekstrategie (nachtelijk):** thermische strategie voor 7 dagen vooruit

### Wat het NIET is
Geen automations in Python. Het verschil:
```
Automation:  IF surplus > 2000W THEN start_dishwasher
EnergyBrain: PLAN dag → bereken optimaal startmoment → start op beste moment
             → leer uit uitkomst → pas plan aan voor morgen
```

### Eerste weken = fallback modus
Het systeem heeft data nodig om te leren. Activering per module:
- Dag 1-14:   Alle intelligence modules gebruiken vaste defaults
- Dag 15+:    ThermalModel actief (7 dagen × 48 samples min.) — LinearRegression 5 features
- Dag 15+:    PatternLearner Laag 1 actief — GradientBoosting basis (14 dagen min.)
- Dag 30+:    PVForecaster kalibratie actief — Ridge Regression
- Dag 30+:    OutcomeTracker start eerste accuracy rapport (30 dagen baseline nodig)
- Dag 90+:    PatternLearner Laag 2 actief — seizoensbewust (90 dagen min.)
- Dag 90+:    ThermalModel upgrade pad — evalueer GradientBoostingRegressor als R² < 0.85
- Dag 365+:   PatternLearner Laag 3 actief — schoolvakantie vs normaal
- Dag 365+:   XGBoost/LightGBM upgrade evaluatie op basis van OutcomeTracker accuracy
- Na V154 firmware: BatteryDispatcher (MPC) actief — Waveshare RS485 adapter al geïnstalleerd,
  enkel V154 firmware fix nodig om schrijven te deblokkeren (V153 write-regressie)

Autonomiedoelstelling:
- Na 90 dagen:  ~75% autonomie (goede basis, soms missen)
- Na 365 dagen: ~92% autonomie (zelfsturend, zelfrapterend, zelfcorrigerend)
- Resterende 8%: onverwachte context (gasten, ziekte) — buiten bereik van meetbare data

---

## 1. Hardware & Netwerk

| Apparaat | Model | IP | Status |
|---|---|---|---|
| Raspberry Pi 5 4GB | HA OS 17.2, Core 2026.4.4 | 192.168.68.62 | ✅ |
| GoodWe omvormer | GW5K-ET 3-fase | 192.168.68.55 | ✅ |
| Marstek batterij | Venus E 5.12kWh | 192.168.68.52 | ⚠️ Lezen OK, schrijven STUB (V153 firmware bug, fix in V154) |
| Plugwise Smile-T | Anna gateway | 192.168.68.61 | ✅ |
| HomeWizard P1 | Smart meter | 192.168.68.54 | ✅ |
| Vaatwasser | Siemens SN65ZX49CE/14 | 192.168.68.60 | ✅ |
| Wasmachine | Siemens WG44B2A5NL/11 | 192.168.68.63 | ✅ |
| Droger | Siemens WQ45B2A5NL/02 | 192.168.68.56 | ✅ |

**Locatie:** Korbeek-lo, Bierbeek, België | lat=50.8597 | lon=4.7628 | postcode 3360
**PV systeem:** 18 × Jinko Tiger 415Wp = 7.47 kWp | zuidgericht | tilt=35° | azimuth=180° (south)
**Kritiek:** Marstek op L1 = enkel L1 pieken afvlakken (zelfde fase als warmtepomp)

---

## 2. Projectstructuur

```
energybrain/
│
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.template
├── .gitignore
│
├── energybrain/
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── models.py
│   ├── exceptions.py
│   │
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   └── orchestrator.py
│   │
│   ├── intelligence/               ← het echte brein
│   │   ├── __init__.py
│   │   ├── thermal_model.py        ← thermisch huismodel (LinearRegression → GBR upgrade pad)
│   │   ├── pv_forecaster.py        ← PV voorspelling + Ridge Regression kalibratie
│   │   ├── pattern_learner.py      ← GradientBoosting DHW/toestel/kookpatronen
│   │   ├── outcome_tracker.py      ← feedback loop + drift detectie + accuracy rapport
│   │   ├── battery_dispatcher.py   ← MPC batterijstrategie (STUB tot V154 firmware fix)
│   │   ├── day_planner.py          ← dagplan + proactieve notificaties
│   │   ├── week_strategist.py      ← 7-daagse thermische strategie
│   │   └── oscillation_detector.py ← voorkomt verwarming/koeling wisselspel
│   │
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── base_agent.py
│   │   ├── goodwe_agent.py
│   │   ├── marstek_agent.py        ← write stub voor V153
│   │   ├── p1_agent.py
│   │   ├── heat_pump_agent.py
│   │   ├── home_connect_agent.py
│   │   ├── ha_agent.py
│   │   ├── weather_agent.py        ← Open-Meteo 7 dagen
│   │   ├── energy_price_agent.py   ← ENTSO-E
│   │   └── notification_agent.py   ← push naar HA Companion app
│   │
│   ├── safety/
│   │   ├── __init__.py
│   │   ├── hard_limits.py          ← onovertreedbare grenzen
│   │   ├── watchdog.py             ← onafhankelijke 5-min monitoring
│   │   └── rollback.py
│   │
│   ├── persistence/
│   │   ├── __init__.py
│   │   ├── database.py             ← SQLite schema + migraties
│   │   ├── state_store.py
│   │   └── learning_store.py
│   │
│   └── utils/
│       ├── __init__.py
│       ├── ha_client.py
│       ├── retry.py
│       └── logging_config.py
│
├── tests/
│   ├── conftest.py
│   ├── test_intelligence/
│   │   ├── test_thermal_model.py
│   │   ├── test_pv_forecaster.py
│   │   ├── test_pattern_learner.py
│   │   ├── test_outcome_tracker.py
│   │   ├── test_battery_dispatcher.py
│   │   ├── test_day_planner.py
│   │   ├── test_week_strategist.py
│   │   └── test_oscillation_detector.py
│   ├── test_agents/
│   │   ├── test_goodwe_agent.py
│   │   ├── test_marstek_agent.py
│   │   ├── test_p1_agent.py
│   │   ├── test_heat_pump_agent.py
│   │   ├── test_home_connect_agent.py
│   │   ├── test_weather_agent.py
│   │   ├── test_energy_price_agent.py
│   │   └── test_notification_agent.py
│   ├── test_safety/
│   │   ├── test_hard_limits.py
│   │   ├── test_watchdog.py
│   │   └── test_rollback.py
│   └── integration/
│       ├── test_full_cycle.py
│       ├── test_day_planning.py
│       ├── test_week_strategy.py
│       └── test_safety_scenarios.py
│
└── scripts/
    ├── check_ha_connection.py
    ├── check_all_agents.py
    ├── simulate_day.py
    └── train_models.py
```

---

## 3. Data Models (models.py)

```python
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
    SET_BATTERY_MODE = "set_battery_mode"      # STUB indien V153
    SET_BATTERY_POWER = "set_battery_power"     # STUB indien V153
    SEND_NOTIFICATION = "send_notification"
    NO_ACTION = "no_action"

class NotificationType(Enum):
    SOLAR_OPPORTUNITY = "solar_opportunity"     # Ochtend: zon verwacht, zet klaar
    APPLIANCE_STARTED = "appliance_started"     # Gestart op zonnestroom
    APPLIANCE_FORCE_STARTED = "force_started"   # Gestart op netstroom (deadline)
    DHW_BOOST = "dhw_boost"
    SAFETY_ALARM = "safety_alarm"               # Altijd verzenden, geen throttle
    DAILY_SUMMARY = "daily_summary"
    WEEK_STRATEGY = "week_strategy"             # Maandag ochtend
    MODEL_DRIFT = "model_drift"                 # Voorspelling minder accuraat
    MONTHLY_REPORT = "monthly_report"           # 1e van de maand — zelfrapportage
    BATTERY_DISPATCH_STUB = "battery_dispatch"  # Dagelijks: wat de batterij ZOU doen


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
    waiting_since: Optional[datetime] = None    # When remote_start became True
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
    current_export_eur_kwh: float   # Eneco ~0.036
    hourly_import_prices: list[float]
    cheap_hours: list[int]
    expensive_hours: list[int]
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ThermalModelParams:
    """Learned thermal parameters of the house."""
    cooling_rate_c_per_hour: float = 0.3    # Default until model trained
    heating_rate_c_per_hour: float = 0.15
    thermal_mass_hours: float = 8.0         # Hours for house to respond
    r2_score: float = 0.0
    samples_count: int = 0
    is_trained: bool = False                # False = using defaults
    model_type: str = "linear"              # "linear" | "gradient_boosting"
    last_updated: datetime = field(default_factory=datetime.now)


@dataclass
class PredictionRecord:
    """Single prediction logged by OutcomeTracker for feedback loop."""
    model_name: str                         # "dhw_demand" | "appliance_loading" | "cooking_peak" | "pv_forecast"
    features: dict                          # Feature values used for prediction
    predicted_value: float                  # Predicted probability or value
    actual_value: Optional[float] = None    # Filled in when outcome is observed
    predicted_at: datetime = field(default_factory=datetime.now)
    outcome_at: Optional[datetime] = None
    is_correct: Optional[bool] = None       # For classifiers: within tolerance?


@dataclass
class AccuracyReport:
    """Monthly accuracy self-report across all models."""
    period_start: datetime
    period_end: datetime
    dhw_accuracy_pct: float
    appliance_loading_accuracy_pct: float
    pv_forecast_accuracy_pct: float
    cooking_peak_accuracy_pct: float
    drift_detected: dict[str, bool]         # model_name → drift_detected
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
    is_stub: bool = True                    # True until V154 firmware fix
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
    is_forced: bool = False         # True = will run regardless of surplus


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
    heating_days: list[int]         # Day indices (0=Mon) where heating planned
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
```

---

## 4. Intelligence Modules

### 4.1 ThermalModel (intelligence/thermal_model.py)

**Doel:** Leer hoe snel het huis opwarmt/afkoelt. Voorspel temp voor 12 uur vooruit.
**Algoritme:** `LinearRegression` op 5 features. Upgrade naar `GradientBoostingRegressor` als R² < 0.85 na 90 dagen.

```python
class ThermalModel:
    """
    Learns thermal behavior of the house via regression.
    
    Training features (every 30 min from HA history):
        - outdoor_temp_c          : primary heat loss driver
        - solar_radiation_w_m2    : solar gain through windows (from Open-Meteo)
        - wind_speed_ms           : convective heat loss amplifier
        - hour_of_day             : captures day/night thermal dynamics
        - hvac_active (bool)      : HVAC contribution
        Target: delta_indoor_per_hour (calculated from consecutive readings)
    
    ⚠️ WHY THESE FEATURES MATTER:
        solar_radiation: sunny winter day can add +2°C even without HVAC
        wind_speed:      strong wind accelerates wall heat loss by 20-30%
        hour_of_day:     nighttime cooling is structurally different from daytime
    
    Model progression:
        Phase 1 (day 15-90):   LinearRegression — simple, interpretable, robust
        Phase 2 (day 90+):     Evaluate R² score. If R² < 0.85: switch to
                               GradientBoostingRegressor(n_estimators=100, max_depth=4)
                               OutcomeTracker drives this upgrade decision automatically.
    
    DHW cooling sub-model (physics-informed):
        dT/dt = -k × (T_water - T_outdoor)   — Newton's law of cooling
        LinearRegression learns constant k from history.
        Features: (dhw_temp_c - outdoor_temp_c) → delta_dhw_per_hour
        This is intentionally kept linear — physics dictates the structure.
    
    Minimum data: 336 samples (7 days × 48 per day)
    Fallback: ThermalModelParams defaults until is_trained=True
    """
    
    MIN_SAMPLES = 336
    R2_UPGRADE_THRESHOLD = 0.85      # Below this after 90 days → switch to GBR
    
    def is_ready(self) -> bool:
        """True when enough data to trust model predictions."""
    
    def predict_temperature(
        self,
        current_indoor_c: float,
        outdoor_forecast: list[float],      # Hourly temps, 12 values
        solar_forecast: list[float],        # Hourly radiation W/m², 12 values
        wind_forecast: list[float],         # Hourly wind speed m/s, 12 values
        hvac_plan: list[bool],              # Planned HVAC on/off per hour
    ) -> list[float]:
        """Predict indoor temp for next 12 hours."""
    
    def predict_dhw_temperature(
        self,
        current_dhw_c: float,
        outdoor_temp_c: float,
        hours_ahead: int,
    ) -> float:
        """
        Predict DHW temperature N hours from now using Newton cooling law.
        Used by DayPlanner to decide when to start DHW boost.
        """
    
    def should_preheat(
        self,
        target_temp_c: float,
        current_temp_c: float,
        desired_time: datetime,
        outdoor_forecast: list[float],
        solar_forecast: list[float],
        wind_forecast: list[float],
    ) -> datetime:
        """When to start heating to reach target at desired_time."""
    
    def update_model(self, observations: list[dict]) -> ThermalModelParams:
        """Retrain with new data. Called daily at 02:00."""
    
    def evaluate_upgrade(self) -> bool:
        """
        Called by OutcomeTracker after 90 days.
        Returns True if GBR upgrade is warranted (R² < threshold).
        """
```

---

### 4.2 PVForecaster (intelligence/pv_forecaster.py)

**Doel:** Voorspel PV-productie voor 7 dagen. Verbeter nauwkeurigheid door ML-kalibratie.
**Algoritme:** Fysisch basismodel + `RidgeRegression` correctiefactor op 5 features.

```python
class PVForecaster:
    """
    Combines Open-Meteo radiation data with Ridge Regression calibration.
    
    Why Ridge (not buckets, not GBR):
        The correction factor is a continuous adjustment to a physical model.
        Features are correlated (temperature correlates with season, cloud_cover
        correlates with radiation). Ridge regularization handles correlated features
        correctly. GBR would overfit on ~365 samples for this specific sub-problem.
    
    Physical base model (POA — Plane of Array):
        direct_radiation × cos(angle_of_incidence)
        + diffuse_radiation × (1 + cos(tilt)) / 2
        panel_tilt=35°, panel_azimuth=180° (south)
    
    Ridge calibration features (5):
        - cloud_cover_pct         : continuous 0-100 (not 3 buckets!)
        - temperature_c           : panel efficiency drops ~0.3%/°C above 25°C
        - wind_speed_ms           : wind cools panels → higher efficiency
        - hour_of_day             : angle of incidence changes throughout day
        - day_of_year             : solar declination changes daily, not per season
    
    Target: actual_kwh / physical_model_kwh (correction factor per observation)
    
    Open-Meteo API:
        lat=50.8597, lon=4.7628, forecast_days=7
        Variables: shortwave_radiation, direct_radiation, diffuse_radiation,
                   cloud_cover, temperature_2m, windspeed_10m
    Cache: 15 minutes
    """
    
    PANEL_KWP = 7.47
    PANEL_AREA_M2 = 18 * 1.722
    BASE_EFFICIENCY = 0.80
    PANEL_TILT_DEG = 35
    PANEL_AZIMUTH_DEG = 180
    MIN_CALIBRATION_DAYS = 30
    
    def forecast(self, days: int = 7) -> WeatherForecast:
        """Ridge-calibrated forecast for next N days."""
    
    def identify_surplus_windows(
        self,
        hourly: list[HourlyForecast],
        min_surplus_w: float,
    ) -> list[SurplusWindow]:
        """Find continuous blocks where estimated surplus > threshold."""
    
    def update_calibration(
        self,
        date: datetime,
        predicted_kwh: float,
        actual_kwh: float,
        avg_cloud_cover: float,
        avg_temp_c: float,
        avg_wind_ms: float,
    ) -> None:
        """
        Add observation to Ridge model and retrain.
        Called at 21:00 with completed day data.
        OutcomeTracker logs predicted vs actual for accuracy reporting.
        """
```

---

### 4.3 PatternLearner (intelligence/pattern_learner.py)

**Doel:** Leer wanneer toestellen gevuld worden, wanneer heet water nodig is, kookpiek.
**Algoritme:** 5× `GradientBoostingClassifier/Regressor` — leert niet-lineaire verbanden tussen weer, weekdag, bezetting en verbruikspatronen.

```python
class PatternLearner:
    """
    Learns usage patterns using GradientBoosting models.
    Three activation layers — system is useful from day 15, improves over time.
    
    WHY GradientBoosting (not RandomForest, not XGBoost, not NN):
        - Captures non-linear feature interactions (cold + weekend ≠ cold + weekday)
        - Well-calibrated probability outputs (important for DayPlanner decisions)
        - Robust on small datasets (365 samples) unlike XGBoost/LightGBM
        - Runs in milliseconds on Pi 5 for inference
        - After 365+ days: OutcomeTracker evaluates XGBoost/LightGBM upgrade
    
    Five separate models:
        dhw_model:        GBC → P(DHW boost needed in next 2h)
        dishwasher_model: GBC → P(dishwasher will be loaded today)
        washing_model:    GBC → P(washing machine will be loaded today)
        dryer_model:      GBC → P(dryer will be loaded today)
        cooking_model:    GBR → expected cooking peak hour (float 15.5-20.5)
    
    Feature vector (9 features, same for all models):
        - weekday             : 0=Mon … 6=Sun
        - hour                : 0-23 (for DHW model only — others are day-level)
        - outdoor_temp_c      : cold → longer/hotter showers, different wash patterns
        - cloud_cover_pct     : overcast → more time at home → earlier appliance use
        - wind_speed_ms       : comfort indicator, correlates with staying home
        - is_school_holiday   : bool — completely different occupancy pattern
        - season_q            : 1-4 (Q1=winter, Q2=spring, Q3=summer, Q4=autumn)
        - baseline_power_w    : average grid draw last 2h — proxy for home occupancy
        - temp_vs_seasonal_avg: how cold/warm vs typical for this time of year
    
    Layer 1 (MIN_DAYS_BASIC=14):
        Trains on weekday + hour + outdoor_temp only (3 features)
        "Winter Wednesday 19u → DHW demand likely"
    
    Layer 2 (MIN_DAYS_SEASONAL=90):
        All 9 features active
        "Cold overcast Saturday in Q1 with high baseline → high DHW demand"
    
    Layer 3 (MIN_DAYS_YEARLY=365):
        Separate model instances per is_school_holiday value
        "School holiday Saturday Q3 vs regular Saturday Q3 → different patterns"
    
    OutcomeTracker feedback:
        Every prediction is logged. Weekly retrain uses outcome accuracy to
        weight recent correct/incorrect predictions in training data.
    """
    
    MIN_DAYS_BASIC = 14
    MIN_DAYS_SEASONAL = 90
    MIN_DAYS_YEARLY = 365
    
    # Model hyperparameters — conservative to prevent overfitting on small data
    GBC_PARAMS = {
        "n_estimators": 100,
        "max_depth": 3,              # Shallow trees prevent overfitting
        "learning_rate": 0.05,
        "min_samples_leaf": 5,       # At least 5 samples per leaf
        "subsample": 0.8,
    }
    
    DEFAULTS = {
        "dhw_high_demand_days": [2, 6],     # Wednesday, Sunday
        "dhw_peak_hour": 20,
        "cooking_peak_start": 17,
        "cooking_peak_end": 19,
    }
    
    def predict_dhw_demand(
        self,
        weekday: int,
        hour: int,
        outdoor_temp_c: float,
        cloud_cover_pct: float,
        wind_speed_ms: float,
        is_school_holiday: bool,
        season_q: int,
        baseline_power_w: float,
        temp_vs_seasonal_avg: float,
    ) -> float:
        """
        Probability (0-1) that DHW boost is needed in next 2 hours.
        Returns default 0.5 if model not yet trained.
        """
    
    def predict_appliance_loading(
        self,
        appliance: ApplianceType,
        weekday: int,
        outdoor_temp_c: float,
        cloud_cover_pct: float,
        wind_speed_ms: float,
        is_school_holiday: bool,
        season_q: int,
        baseline_power_w: float,
        temp_vs_seasonal_avg: float,
    ) -> float:
        """Probability (0-1) that appliance will be loaded today."""
    
    def get_cooking_peak(
        self,
        weekday: int,
        outdoor_temp_c: float,
        cloud_cover_pct: float,
        is_school_holiday: bool,
        season_q: int,
    ) -> tuple[int, int]:
        """
        Returns (start_hour, end_hour) of predicted cooking peak.
        Used by CapacityTariffGuard — replaces hardcoded 17:00-18:30.
        Falls back to DEFAULTS if model not trained.
        """
    
    def update_patterns(self, days_of_history: int = 90) -> None:
        """
        Retrain all 5 models from last N days of history.
        Called weekly on Sunday at 02:00.
        Uses MIN_DAYS_BASIC/SEASONAL/YEARLY to determine active features.
        OutcomeTracker feedback weights are applied during retraining.
        """
    
    def get_feature_importances(self) -> dict[str, dict[str, float]]:
        """
        Returns feature importance per model for self-reporting.
        Example: {"dhw_model": {"outdoor_temp_c": 0.34, "weekday": 0.28, ...}}
        Included in monthly AccuracyReport for transparency.
        """
```

---

### 4.4 OscillationDetector (intelligence/oscillation_detector.py)

**Doel:** Detecteer en voorkom het energie-verspillende verwarming/koeling wisselspel.

```python
class OscillationDetector:
    """
    Detects the heating-cooling oscillation problem and prevents it.
    
    Problem scenario:
        Mon: 26°C → system cools house (costs energy)
        Wed: 14°C → system heats house (costs energy)  
        Fri: 26°C → system cools again (wastes energy)
        
        Reality: thermal mass of floor would have handled Wed naturally.
        The system wasted energy on unnecessary interventions.
    
    Detection:
        Count HVAC mode switches in last 7 days.
        If > SWITCH_THRESHOLD AND outdoor temp swing > TEMP_SWING_THRESHOLD:
            Set oscillation_risk = True
            Freeze HVAC strategy for FREEZE_HOURS
    
    During freeze:
        - No HVAC mode changes allowed (except hard limits)
        - WeekStrategist enters neutral mode
        - User notified of situation
    """
    
    SWITCH_THRESHOLD = 3            # Mode switches in 7 days
    TEMP_SWING_THRESHOLD_C = 8.0
    FREEZE_HOURS = 48
    
    def check(self, hvac_history: list[dict], outdoor_temps: list[float]) -> bool:
        """Returns True if oscillation pattern detected."""
    
    def is_frozen(self) -> bool:
        """True if strategy changes are blocked."""
```

---

### 4.5 OutcomeTracker (intelligence/outcome_tracker.py)

**Doel:** Sluit de feedback loop — vergelijk elke voorspelling met de werkelijkheid, detecteer modeldrift, genereer maandrapport.

```python
class OutcomeTracker:
    """
    Closes the feedback loop for all ML models in EnergyBrain.
    
    Without this, GradientBoosting models learn patterns but never know
    if their decisions were GOOD. This module transforms EnergyBrain from
    a pattern-follower into a self-improving system.
    
    Flow per prediction:
        1. Model makes prediction → OutcomeTracker.log_prediction()
        2. Reality unfolds (DHW temp observed, appliance loaded or not, etc.)
        3. OutcomeTracker.log_outcome() fills in actual_value
        4. is_correct calculated: |predicted - actual| < tolerance
        5. PatternLearner uses accuracy feedback in weekly retraining
    
    Drift detection (rolling 14-day window):
        Compare accuracy of last 14 days vs previous 30-day baseline.
        If accuracy drops > DRIFT_THRESHOLD_PCT:
            → Send notification to user
            → Fall back to conservative defaults for that model
            → Log drift event for monthly report
        
        "⚠️ EnergyBrain merkt dat DHW-voorspellingen minder kloppen.
         Zijn uw gewoonten veranderd? Het systeem past zich aan."
    
    Monthly accuracy report (generated on 1st of each month at 07:00):
        Sent as push notification + stored in DB.
        Includes: accuracy per model, drift events, estimated savings,
                  feature importances (what does the model use most?),
                  failed actions (appliance not ready, DHW too late).
    
    ThermalModel upgrade trigger:
        After 90 days: evaluate R² score.
        If R² < ThermalModel.R2_UPGRADE_THRESHOLD: trigger GBR upgrade.
        Log upgrade event in AccuracyReport.
    
    XGBoost/LightGBM upgrade evaluation (365+ days):
        Compare PatternLearner accuracy in last 90 days.
        If GBC accuracy has plateaued (< 2% improvement in 90 days):
            → Suggest XGBoost upgrade in monthly report
            → Do NOT auto-upgrade — requires manual confirmation
    """
    
    DRIFT_THRESHOLD_PCT = 15.0      # Accuracy drop that triggers drift alert
    CORRECTION_TOLERANCE = {
        "dhw_demand":          0.15,    # ±15% probability tolerance
        "appliance_loading":   0.20,    # ±20% probability tolerance
        "cooking_peak":        1.0,     # ±1 hour tolerance
        "pv_forecast":         0.10,    # ±10% kWh tolerance
    }
    
    def log_prediction(
        self,
        model_name: str,
        features: dict,
        predicted_value: float,
    ) -> str:
        """Log prediction, returns prediction_id for later outcome linking."""
    
    def log_outcome(
        self,
        prediction_id: str,
        actual_value: float,
    ) -> None:
        """Link actual outcome to prediction. Calculates is_correct."""
    
    def check_drift(self) -> dict[str, bool]:
        """
        Returns {model_name: drift_detected} for all models.
        Called daily at 02:00 before PatternLearner retraining.
        Sends notification if new drift detected since last check.
        """
    
    def get_accuracy_report(self, period_days: int = 30) -> AccuracyReport:
        """
        Full accuracy report for last N days.
        Called monthly for push notification.
        """
    
    def trigger_thermal_model_upgrade(self, thermal_model: "ThermalModel") -> bool:
        """
        Called after 90 days. Returns True if upgrade was performed.
        Logs upgrade event.
        """
    
    def get_model_feedback_weights(self, model_name: str) -> list[float]:
        """
        Returns sample weights for PatternLearner retraining.
        Recent correctly-predicted samples get higher weight.
        Recent incorrect samples get lower weight (but not zero — preserve diversity).
        """
```

---

### 4.6 BatteryDispatcher (intelligence/battery_dispatcher.py)

**Doel:** Optimale 24-uurs laad/ontlaadstrategie via Model Predictive Control (MPC).
**Status: STUB tot Marstek V154 firmware — berekent maar voert NIET uit.**
**V153 write-bug:** RS485 adapter aanwezig, schrijven geblokkeerd door firmware regressie.

```python
class BatteryDispatcher:
    """
    Calculates optimal 24-hour battery charge/discharge schedule using MPC.
    
    ⚠️ STUB STATUS: Marstek RS485 SCHRIJVEN kapot in V153 firmware (bekende regressie-bug).
    RS485 lezen werkt (Waveshare adapter geïnstalleerd). Schrijven geblokkeerd door firmware.
    Fix verwacht in V154. Alle output wordt GELOGD maar NIET uitgevoerd tot MARSTEK_WRITE_ENABLED=true.
    Dit laat toe het algoritme weken te valideren vóór het live gaat.
    
    Why MPC (not ML) for battery dispatch:
        Battery dispatch is an OPTIMIZATION problem, not a pattern recognition problem.
        MPC solves: "given known constraints and forecasts, what is the optimal schedule?"
        This is deterministic, auditable, and explainable — critical for a system
        that controls a €3000+ battery asset.
    
    MPC inputs (all from other modules):
        - PV forecast 24h      : PVForecaster.forecast()
        - Expected consumption  : PatternLearner predictions × typical power
        - Current SoC           : MarstekAgent.get_state().soc_pct
        - Capacity tariff state : CapacityTariffGuard.get_rolling_12month_avg_peak_kw()
        - Energy price          : EnergyPriceAgent (static now, dynamic future)
        - Battery constraints   : max charge/discharge rate, min SoC (10%)
    
    Optimization objective:
        Minimize: grid_import_cost + capacity_tariff_impact
        Subject to:
            - SoC stays between 10% and 95%
            - Charge rate ≤ max_charge_w
            - Discharge rate ≤ max_discharge_w
            - Peak demand ≤ current_monthly_peak_kw (avoid raising tariff baseline)
    
    Implementation: scipy.optimize.linprog (linear programming)
        Linear because costs are linear in kWh and kW — no need for complex solver.
        Runs in < 100ms on Pi 5 for 24-hour horizon with 15-min timesteps (96 steps).
    
    Output: BatteryDispatchPlan with hourly_target_w
        Positive = charge (absorb surplus or cheap grid)
        Negative = discharge (cover consumption, avoid peak)
    
    Stub behavior (MARSTEK_WRITE_ENABLED=false):
        Plan is calculated and logged every hour.
        Log format: "STUB: would charge 1200W at 13:00, discharge 800W at 19:00"
        This lets you validate the algorithm for weeks before enabling real control.
    
    Activation checklist (wacht op V154 firmware):
        □ V154 firmware OTA update ontvangen van Marstek
        □ Controleer in HA: sensor.marstek_venuse_firmware_version = 154
        □ Test RS485 write: register 42000 ← 21930 (enable control mode), verificeer respons
        □ Test force charge: register 42010 ← 1, observeer of batterij fysiek begint te laden
        □ Set MARSTEK_WRITE_ENABLED=true in .env
        □ Monitor eerste 7 dagen via dagelijkse samenvatting notificatie
        □ Vergelijk geplande vs werkelijke SoC in ochtendbericht
        
        CONTEXT: V153 heeft bekende write-regressie (werkte op V151, kapot in V153).
        De Waveshare USB-RS485 adapter is al geïnstalleerd en lezen werkt.
        Enkel schrijven is geblokkeerd door firmware bug — geen nieuwe hardware nodig.
    """
    
    STUB_MODE = True    # Controlled by MARSTEK_WRITE_ENABLED env var
    TIMESTEP_MINUTES = 15
    HORIZON_HOURS = 24
    MIN_SOC_PCT = 10.0
    MAX_SOC_PCT = 95.0
    
    def calculate_dispatch_plan(
        self,
        pv_forecast_w: list[float],         # 96 values (15-min intervals)
        consumption_forecast_w: list[float], # 96 values from PatternLearner
        current_soc_pct: float,
        current_monthly_peak_kw: float,
        import_price_eur_kwh: float,
        export_price_eur_kwh: float,
    ) -> BatteryDispatchPlan:
        """
        Solve MPC optimization. Returns plan regardless of stub mode.
        Execution is blocked by stub mode, logging always happens.
        """
    
    async def execute_plan(self, plan: BatteryDispatchPlan) -> None:
        """
        Execute current timestep of plan via MarstekAgent.
        STUB: logs intended action, does not write to Marstek.
        LIVE (RS485 active): calls marstek_agent.set_power_w(target_w)
        """
    
    def explain_plan(self, plan: BatteryDispatchPlan) -> str:
        """
        Human-readable plan summary for daily notification.
        Example:
        '🔋 Batterijplan morgen:
         09:00-13:00: Laden van zonnepanelen (+1.2kW)
         17:00-20:00: Ontladen voor kookpiek (-0.8kW)
         Verwachte besparing: €0.34 | Piekbesparing: 0.8kW'
        [STUB — plan berekend, nog niet uitgevoerd]
        """
```

---

### 4.5 DayPlanner (intelligence/day_planner.py)

**Doel:** Dagplan + proactieve ochtendnotificatie als er zon verwacht wordt.

```python
class DayPlanner:
    """
    Creates optimal day plan at 06:30. Updates every 15 min.
    
    Planning process:
    1. Get calibrated PV forecast
    2. Identify surplus windows
    3. Get pattern predictions for today's appliances
    4. Schedule tasks in priority order within surplus windows
    5. Set deadlines for tasks that won't fit in surplus windows
    6. Send morning push notification if meaningful surplus expected
    
    Priority order:
    1. DHW boost (daily critical, first priority)
    2. Dishwasher
    3. Washing machine
    4. Dryer (flexible, largest consumer)
    5. Battery charging
    6. HVAC +0.5°C preloading (thermal storage)
    
    Two-pass decision per cycle:
    Pass 1: Solar optimization (ideal timing)
    Pass 2: Deadline enforcement (guarantee appliances always run)
    """
    
    APPLIANCE_DEADLINES = {
        ApplianceType.DISHWASHER:      {"max_wait_h": 4,  "hard_deadline": time(20, 0)},
        ApplianceType.WASHING_MACHINE: {"max_wait_h": 6,  "hard_deadline": time(20, 0)},
        ApplianceType.DRYER:           {"max_wait_h": 8,  "hard_deadline": time(21, 0)},
    }
    
    # Morning notification only if:
    NOTIFICATION_MIN_SURPLUS_HOURS = 1.5
    NOTIFICATION_MIN_SURPLUS_W = 1500
    
    def create_day_plan(self, state: SystemState) -> DayPlan:
        """Full day plan. Called at 06:30."""
    
    def update_plan(self, current_plan: DayPlan, state: SystemState) -> DayPlan:
        """Update plan if forecast deviated > 10%. Called every 15 min."""
    
    def should_force_start(
        self, appliance: ApplianceType, waiting_since: datetime
    ) -> tuple[bool, str]:
        """
        Returns (should_force, reason).
        Force if: waited > max_wait_h OR current_time >= hard_deadline.
        """
    
    def build_morning_notification(self, plan: DayPlan, state: SystemState) -> str:
        """
        Build proactive push notification.
        
        Example:
        '☀️ Goede zon verwacht vandaag!
         Surplus: 11:00-15:30 (gem. 2.800W, ~13kWh)
         
         Als je klaar staat starten we automatisch:
         🧺 Wasmachine
         🍽️ Vaatwasser
         
         Zet toestellen klaar en activeer remote start.'
        
        Only sent if surplus window >= 1.5h AND at least 1 appliance
        loading probability > 0.5 (from PatternLearner).
        """
```

---

### 4.6 WeekStrategist (intelligence/week_strategist.py)

**Doel:** Beslis 7 dagen vooruit over thermische strategie. Voorkom nutteloos koelen/verwarmen.

```python
class WeekStrategist:
    """
    Calculates 7-day thermal strategy every night at 02:30.
    
    ⚠️ COOLING NOT AVAILABLE:
        Thermastage cooling feature requires €300 activation via Thercon Belgium.
        Not activated → cooling_days always returns [].
        Anna cooling mode can only be toggled via physical button on device,
        NOT via Home Assistant or the Plugwise app.
        WeekStrategist plans heating and neutral only.
    
    Key decisions:
    
    Heating/preloading decision (ACTIVE):
        Worthwhile if:
        - Cold period incoming (<10°C outdoor)
        - Enough PV surplus to preload thermal mass economically
        - ThermalModel confirms house won't recover naturally in time
    
    Cooling decision (DISABLED — no Thermastage feature):
        cooling_days always = [] until feature activated.
        Natural thermal mass handles summer heat passively.
    
    Oscillation override:
        If OscillationDetector.is_frozen(): return neutral strategy
    
    Uses ThermalModel.predict_temperature() to simulate each scenario
    before deciding — never acts on temperature alone.
    """
    
    COOLING_ENABLED = False          # Pending test — see section 14.2 for test procedure
    COOLING_MIN_DAYS = 3
    COLD_THRESHOLD_C = 5.0          # Drop that counts as "cold period"
    LOOKFORWARD_DAYS = 5
    
    def calculate_strategy(
        self,
        thermal_model: ThermalModel,
        forecaster: PVForecaster,
        oscillation_detector: OscillationDetector,
    ) -> WeekStrategy:
        """Full 7-day strategy. Simulates each scenario before deciding."""
    
    def explain_strategy(self, strategy: WeekStrategy) -> str:
        """
        Human-readable explanation for Monday morning notification.
        
        Example:
        '📊 Weekstrategie:
         Ma-Di: Warm, koeling niet nodig (huis koelt vanzelf af)
         Wo-Do: Koud, VVW voorladen op zonnestroom
         Vr-Zo: Stabiel, geen actie nodig'
        """
```

---

## 5. Safety System

### 5.1 HardLimits (safety/hard_limits.py)

**Nooit overschrijdbaar — zelfs niet door het brein.**

```python
HARD_LIMITS = {
    "indoor_temp_min_winter_c": 17.0,
    "indoor_temp_max_summer_c": 26.0,
    "hvac_setpoint_max_c": 22.5,          # Vloerverwarming oververhitting
    "hvac_setpoint_min_c": 16.0,          # Vorstbescherming
    "hvac_max_step_per_cycle_c": 0.5,     # Max per beslissingscyclus
    "dhw_min_temp_before_evening_c": 45.0, # Boiler min 45°C voor 18:00
    "battery_soc_min_pct": 10.0,
    "outdoor_frost_threshold_c": -2.0,    # Onder dit: geen inmenging (pomp bescherming)
}

# ⚠️ PRIORITEITSREGELS bij conflicterende limieten:
# Regel 1 (HOOGSTE PRIO): indoor_temp < indoor_temp_min_winter_c → force heat
# Regel 2:                outdoor < outdoor_frost_threshold AND indoor >= 17°C → geen inmenging
# Bij conflict: indoor_temp wint (bewoners veiligheid > pomp bescherming)
# Voorbeeld: -3°C buiten + 16.5°C binnen → force heat (override frost rule)
# Voorbeeld: -3°C buiten + 19°C binnen → geen inmenging (frost rule actief)

def validate_action(action: Action, state: SystemState) -> tuple[bool, str]:
    """
    Check all hard limits. Called before EVERY action execution.
    Returns (is_safe, reason_if_unsafe).
    """
```

### 5.2 Watchdog (safety/watchdog.py)

**Volledig onafhankelijk van het brein. Aparte asyncio task. 5-min cyclus.**

```python
class Watchdog:
    """
    Independent safety monitor. Never depends on brain decisions.
    
    Checks every 5 minutes:
    1. indoor_temp < 17°C AND outdoor_temp < 15°C → force heat + SAFETY_ALARM push
       (Temperature-based, no need to define "winter" — works year-round when needed)
    2. DHW temp < 40°C after 17:00 → force DHW boost + SAFETY_ALARM push
    3. Battery SoC < 8% → force passive + SAFETY_ALARM push
    4. HVAC off > 4h AND outdoor_temp < 5°C → force heat + WARNING
       (Pump frost protection — kicks in when long idle in cold conditions)
    5. Brain no decision > 10 min → WARNING (brain process dead?)
    6. Marstek CT clamp disconnected → WARNING (battery cannot measure grid, not optimizing)
    
    Safety actions bypass brain and execute directly.
    SAFETY_ALARM notifications are never throttled.
    """
    
    INTERVAL_SECONDS = 300
    
    async def run_forever(self) -> None:
        """Separate task, never stops."""
    
    async def check_all(self, state: SystemState) -> list[Action]:
        """Returns safety actions needed. Empty list = all OK."""
```

### 5.3 Rollback (safety/rollback.py)

```python
class RollbackManager:
    """
    Tracks reversible actions and auto-rolls back after timeout.
    
    Example use case:
        Brain sets HVAC to 22°C for solar preloading at 11:00.
        If PV forecast was wrong: roll back to 21°C after 4 hours.
    
    Every Action with rollback_after_minutes set is registered here.
    """
```

---

## 6. Agent Specificaties

### 6.1 GoodWeAgent

```python
# Alle entiteiten bevestigd (2026-05-06)
# ⚠️ NACHT GEDRAG: GoodWe gaat in slaapstand bij geen productie
# Alle realtime sensors worden 'unavailable' na zonsondergang → normaal, handle as 0W
#
# ⚠️ AC-GEKOPPELDE BATTERIJ: Marstek Venus is AC-gekoppeld — GoodWe ziet de batterij NIET
# sensor.goodwe_battery_power / battery_mode = altijd 0 / unknown → NIET GEBRUIKEN
# Gebruik MarstekAgent als primaire batterijbron, P1Agent voor netto grid.
#
# ⚠️ PEAK_SHAVING MODUS: NIET BESCHIKBAAR zonder SolarGo app configuratie
# select.goodwe_bedrijfsmodus_omvormer = "peak_shaving" heeft GEEN effect zonder
# voorafgaande configuratie in SolarGo (reserved SOC, schema, limieten).
# SolarGo app is niet geïnstalleerd → gebruik CapacityTariffGuard als enige pieklimiter.

READ_ENTITIES = {
    # PV productie (realtime — unavailable 's nachts → gebruik 0W als fallback)
    "pv_power_w":           "sensor.goodwe_pv_power",
    "pv1_power_w":          "sensor.goodwe_pv1_power",
    "pv2_power_w":          "sensor.goodwe_pv2_power",

    # PV dagtellingen (beschikbaar ook 's nachts)
    "today_pv_kwh":         "sensor.goodwe_today_s_pv_generation",
    "total_pv_kwh":         "sensor.goodwe_total_pv_generation",

    # Huisverbruik
    "house_consumption_w":  "sensor.goodwe_house_consumption",
    "today_load_kwh":       "sensor.goodwe_today_load",
    "total_load_kwh":       "sensor.goodwe_total_load",

    # Grid via GoodWe meter (backup voor P1 — P1 is primair)
    "meter_total_w":        "sensor.goodwe_meter_active_power_total",
    "meter_l1_w":           "sensor.goodwe_meter_active_power_l1",   # Per fase
    "meter_l2_w":           "sensor.goodwe_meter_active_power_l2",
    "meter_l3_w":           "sensor.goodwe_meter_active_power_l3",
    "today_export_kwh":     "sensor.goodwe_today_energy_export",
    "today_import_kwh":     "sensor.goodwe_today_energy_import",
    "total_export_kwh":     "sensor.goodwe_total_energy_export",
    "total_import_kwh":     "sensor.goodwe_total_energy_import",

    # ⚠️ Batterij via GoodWe: NIET GEBRUIKEN — Marstek is AC-gekoppeld, GoodWe ziet 0
    # "battery_power_w":    DISABLED — gebruik sensor.marstek_venuse_power
    # "battery_mode":       DISABLED — gebruik select.marstek_venuse_operating_mode

    # Status
    "work_mode":            "sensor.goodwe_work_mode",
    "grid_mode":            "sensor.goodwe_grid_mode",
    "error_codes":          "sensor.goodwe_error_codes",
}

WRITE_ENTITIES = {
    # ⚠️ peak_shaving NIET bruikbaar zonder SolarGo configuratie
    # ⚠️ ECO modus = zelfde als GENERAL zonder DC-gekoppelde batterij (Marstek is AC)
    # AANBEVELING: gebruik GENERAL als standaard modus
    # EnergyBrain gebruikt export_limit_w als primair stuurmiddel:
    #   - export_limit_w = 0:     onbeperkte export (default)
    #   - export_limit_w = X:     beperk export zodat meer PV via net naar Marstek gaat
    "operating_mode":  ("select", "select_option", "select.goodwe_bedrijfsmodus_omvormer"),
    # Export limiet W (0 = geen limiet)
    "export_limit_w":  ("number", "set_value", "number.goodwe_net_exportlimiet"),
    # Ontlaaddiepte % (huidig 60%)
    "discharge_depth": ("number", "set_value", "number.goodwe_depth_of_discharge_on_grid"),
}

# NOOT: P1Agent is primaire bron voor grid power
# GoodWe meter = backup + bron voor per-fase data (L1/L2/L3)
# MarstekAgent is primaire bron voor batterijdata
# GoodWe operating_mode = GENERAL bij startup instellen via StartupRecovery
```

### 6.2 P1Agent (HomeWizard)

```python
# Alle entiteiten bevestigd (2026-05-06)
# Slimme meter: Fluvius 253769484_A (DSMR v5.0)

READ_ENTITIES = {
    # Huidig vermogen — PRIMAIR voor GridState
    "power_w":          "sensor.p1_meter_vermogen",          # 244W (pos=afname, neg=injectie)
    "power_l1_w":       "sensor.p1_meter_vermogen_fase_1",   # 73W
    "power_l2_w":       "sensor.p1_meter_vermogen_fase_2",   # 21W
    "power_l3_w":       "sensor.p1_meter_vermogen_fase_3",   # 149W

    # ⭐ Capaciteitstarief — Fluvius berekent dit zelf!
    "avg_15min_w":      "sensor.p1_meter_gemiddeld_verbruik",          # lopend 15-min gemiddelde
    "peak_month_w":     "sensor.p1_meter_piekverbruik_huidige_maand",  # 7215W huidig maandpiek

    # Huidig tarief (1=goedkoop, 2=duur)
    "current_tariff":   "sensor.p1_meter_tarief",            # 1, 2, 3 of 4

    # Energie totalen (historiek)
    "total_export_kwh": "sensor.p1_meter_energie_export",    # 8336.331 kWh
    "export_t1_kwh":    "sensor.p1_meter_energie_export_tarief_1",
    "export_t2_kwh":    "sensor.p1_meter_energie_export_tarief_2",
    "import_t1_kwh":    "sensor.p1_meter_energie_import_tarief_1",
    "import_t2_kwh":    "sensor.p1_meter_energie_import_tarief_2",
}

# Positief = afname van net | Negatief = injectie naar net
# GridState.surplus_w = max(0, -power_w)

# ⭐ CAPACITEITSTARIEF:
# sensor.p1_meter_piekverbruik_huidige_maand = maandpiek (Fluvius berekening)
# Huidig: 7.215 kW → ~€28/maand capaciteitstoeslag bij €47.50/kW/jaar (12-maands rolling avg)
# CapacityTariffGuard monitort avg_15min_w — als dit richting piek_maand gaat: stagger actief
```

### 6.3 WeatherAgent

```python
# Open-Meteo — 7 dagen, geen API key
# Location uit .env: LATITUDE, LONGITUDE
# Tilt=35° (standaard Belgische installatie), Azimuth=180° (south)
# direct_radiation + diffuse_radiation voor correcte POA (Plane of Array) berekening
def build_api_url(latitude: float, longitude: float) -> str:
    return (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={latitude}&longitude={longitude}"
        "&hourly=shortwave_radiation,direct_radiation,diffuse_radiation"
        "&hourly=cloud_cover,temperature_2m,windspeed_10m"
        "&forecast_days=7&timezone=Europe%2FBrussels"
    )
# POA irradiance (Plane of Array) berekening in PVForecaster:
#   POA ≈ direct_radiation × cos(angle_of_incidence) + diffuse_radiation × (1 + cos(tilt)) / 2
#   angle_of_incidence = f(solar_zenith, solar_azimuth, panel_tilt=35°, panel_azimuth=180°)
#   GHI (shortwave_radiation) als fallback wanneer POA-berekening faalt
PANEL_TILT_DEG = 35
PANEL_AZIMUTH_DEG = 180    # 180° = south (meteorological convention)
CACHE_MINUTES = 15
```

### 6.4 NotificationAgent

```python
# HA Companion app push notifications
# Service: notify.mobile_app_{NOTIFICATION_DEVICE}

TEMPLATES = {
    NotificationType.SOLAR_OPPORTUNITY: {
        "title": "☀️ Goede zon verwacht vandaag",
        "body": (
            "Surplus venster: {start}–{end} (gem. {avg_w}W)\n\n"
            "Als je klaar staat starten we automatisch:\n"
            "{appliance_list}\n\n"
            "Zet toestellen klaar en activeer remote start."
        ),
        "send_at": "06:30",
        "throttle": "1_per_day",
    },
    NotificationType.APPLIANCE_STARTED: {
        "title": "🌞 {appliance} gestart op zonnestroom",
        "body": "Overschot: {surplus_w}W",
        "throttle": "1_per_appliance_per_run",  # Resets when appliance finishes
    },
    NotificationType.APPLIANCE_FORCE_STARTED: {
        "title": "⏰ {appliance} gestart (deadline)",
        "body": "Geen voldoende zon. Gestart op netstroom na {wait_h}u wachten.",
        "throttle": "1_per_appliance_per_run",  # Resets when appliance finishes
    },
    NotificationType.MONTHLY_REPORT: {
        "title": "📊 EnergyBrain maandrapport — {month}",
        "throttle": "1_per_month",
        # Body template (generated by OutcomeTracker.get_accuracy_report()):
        # "Voorspellingsnauwkeurigheid:
        #   DHW timing:        {dhw_pct}%  {dhw_icon}
        #   Waspatroon:        {wash_pct}% {wash_icon}
        #   PV forecast:       {pv_pct}%   {pv_icon}
        #   Kookpiek:          {cook_pct}% {cook_icon}
        #
        # Besparingen deze maand:
        #   Geschatte besparing: €{savings}
        #   Capaciteitspiek:     {peak_kw} kW (rolling avg)
        #
        # Modellen leren het meest van:
        #   DHW: {top_feature_dhw}
        #   Was: {top_feature_wash}
        #
        # {drift_warning_if_any}
        # {upgrade_suggestion_if_any}"
    },
    NotificationType.DHW_BOOST: {
        "title": "🚿 Boiler verwarmt op zonnestroom",
        "body": "Overschot: {surplus_w}W",
        "throttle": "1_per_day",
    },
    NotificationType.SAFETY_ALARM: {
        "title": "🚨 EnergyBrain veiligheidsalarm",
        "body": "{message}",
        "throttle": "none",             # Always send immediately
        "priority": "high",
    },
    NotificationType.WEEK_STRATEGY: {
        "title": "📊 Weekstrategie bijgewerkt",
        "body": "{strategy_explanation}",
        "send_at": "07:00",
        "send_on": "monday",
        "throttle": "1_per_week",
    },
    NotificationType.DAILY_SUMMARY: {
        "title": "📈 EnergyBrain dagrapport",
        "body": (
            "☀️ Geproduceerd: {pv_kwh}kWh\n"
            "🏠 Eigenverbruik: {self_use_pct}%\n"
            "🔋 Batterij: {battery_soc}%\n"
            "💰 Geschatte besparing: €{savings:.2f}"
        ),
        "send_at": "21:30",
        "throttle": "1_per_day",
    },
}
```

### 6.5 MarstekAgent

```python
# Alle entiteiten bevestigd (2026-05-06)
# Integratie: jaapp/ha-marstek-local-api v1.1.0 (up to date)
# Firmware: V153 (bevestigd via sensor.marstek_venuse_firmware_version = 153)

# ⚠️ CT CLAMP NIET VERBONDEN:
# binary_sensor.marstek_venuse_ct_connected = off
# sensor.marstek_venuse_ct_phase_a/b/c_power = unknown
# Dit is de oorzaak van de meetproblemen — CT clamp meet netafname niet

# ⚠️ WRITE STUB: MARSTEK_WRITE_ENABLED=false tot V154 firmware beschikbaar
#
# SITUATIE (bevestigd door directe tests):
# - Lezen via WiFi UDP (jaapp/ha-marstek-local-api): werkt ✅
# - RS485 Modbus lezen: werkt ✅ (Waveshare adapter geïnstalleerd)
# - RS485 Modbus SCHRIJVEN: KAPOT in V153 — dit is een bekende regressie-bug
#   V153 firmware heeft schrijven via RS485 én UDP controle stukgemaakt.
#   Op V151 en eerder werkte schrijven wél.
#   Fix verwacht in V154 firmware.
#
# CONTEXT: Online sources die claimen dat schrijven werkt, beschrijven:
#   - Venus E hardware met oudere firmware (V147-V151) — andere situatie
#   - Venus E V3 hardwareversie met native LAN Modbus — compleet ander apparaat
#   Niet van toepassing op V153 Venus E V2 zoals geïnstalleerd bij David.
#
# Schrijfregisters (klaar voor zodra V154 beschikbaar is):
#   1. Activeer RS485 control:    register 42000 ← 21930 (0x55AA)
#   2. Set charge/discharge power: register 42020 (charge W) / 42021 (discharge W)
#   3. Start mode:                  register 42010 ← 1 (charge), 2 (discharge), 0 (stop)
#   4. Stop:                        register 42010 ← 0, register 42000 ← 21947 (0x55BB)

READ_ENTITIES = {
    # Batterij status — primaire data
    "soc_pct":              "sensor.marstek_venuse_state_of_charge",  # 80%
    "power_w":              "sensor.marstek_venuse_power",            # -1.8W (neg=ontladen)
    "power_in_w":           "sensor.marstek_venuse_power_in",         # W laden
    "power_out_w":          "sensor.marstek_venuse_power_out",        # W ontladen
    "voltage_v":            "sensor.marstek_venuse_voltage",          # 53.19V
    "current_a":            "sensor.marstek_venuse_current",          # 0.0A
    "temperature_c":        "sensor.marstek_venuse_battery_temperature",  # 24.0°C
    "state":                "sensor.marstek_venuse_state",            # discharging
    "discharge_flag":       "sensor.marstek_venuse_discharge_flag",   # True

    # Capaciteit
    "rated_capacity_kwh":   "sensor.marstek_venuse_rated_capacity",   # 5.12 kWh
    "available_kwh":        "sensor.marstek_venuse_available_capacity",  # 1.024 kWh
    "remaining_kwh":        "sensor.marstek_venuse_remaining_capacity",  # ⚠️ buggy V153

    # Vermogen & grid (Marstek perspectief)
    "grid_power_w":         "sensor.marstek_venuse_grid_power",       # 0W
    "solar_power_w":        "sensor.marstek_venuse_solar_power",      # 0W
    "off_grid_power_w":     "sensor.marstek_venuse_off_grid_power",   # 0W

    # Totale energie (historiek)
    "total_grid_export_kwh":"sensor.marstek_venuse_total_grid_export",  # 1285.45 kWh
    "total_grid_import_kwh":"sensor.marstek_venuse_total_grid_import",  # 1616.43 kWh

    # Modus
    "operating_mode":       "select.marstek_venuse_operating_mode",   # Auto|AI|Manual|Passive
    # ⚠️ MODUS WAARSCHUWING:
    # Oorspronkelijke modus: AI + CT clamp
    # HA heeft dit zonder reden gewijzigd naar: Manual + CT clamp
    # In Manual modus doet Marstek NIETS tenzij expliciet aangestuurd via RS485/API
    # Met MARSTEK_WRITE_ENABLED=false (huidige staat) = batterij staat volledig stil
    # → geen laden, geen ontladen → dagelijkse kost van ~€0.25-0.50 aan gemiste besparing
    #
    # ACTIE VEREIST bij installatie:
    #   Controleer select.marstek_venuse_operating_mode in HA
    #   Als 'Manual': zet terug naar 'Auto' totdat EnergyBrain writes actief zijn
    #   Als EnergyBrain V154 writes actief: dan terug naar 'Manual' voor volledige controle
    # (sensor.marstek_venuse_operating_mode toont 'unknown' — gebruik select!)

    # CT clamp status (connectiviteitsprobleem detectie)
    "ct_connected":         "binary_sensor.marstek_venuse_ct_connected",  # off = probleem!
    "ct_phase_a_w":         "sensor.marstek_venuse_ct_phase_a_power",
    "ct_phase_b_w":         "sensor.marstek_venuse_ct_phase_b_power",
    "ct_phase_c_w":         "sensor.marstek_venuse_ct_phase_c_power",
    "ct_total_w":           "sensor.marstek_venuse_ct_total_power",

    # Bluetooth & wifi status (voor watchdog)
    "charging_enabled":     "binary_sensor.marstek_venuse_charging_enabled",
    "discharging_enabled":  "binary_sensor.marstek_venuse_discharging_enabled",
    "firmware_version":     "sensor.marstek_venuse_firmware_version",  # 153
    "last_message_s":       "sensor.marstek_venuse_last_message_received",  # 0s
    "wifi_signal_dbm":      "sensor.marstek_venuse_wifi_signal_strength",   # 35 dBm
}

WRITE_ENTITIES = {
    # Modus instellen (GESTUBD als MARSTEK_WRITE_ENABLED=false)
    "operating_mode": ("select", "select_option", "select.marstek_venuse_operating_mode"),
    # Opties: Auto | AI | Manual | Passive
}

# RS485 Modbus registers (klaar voor V154 — lezen werkt nu al, schrijven geblokkeerd door V153 bug):
RS485_REGISTERS = {
    "control_mode":      42000,  # on=21930, off=21947
    "force_mode":        42010,  # 0=stop, 1=charge, 2=discharge
    "charge_soc_target": 42011,
    "force_charge_w":    42020,
    "force_discharge_w": 42021,
}

# ⚠️ KRITIEKE NOOT — SoC betrouwbaarheid:
# sensor.marstek_venuse_state_of_charge = 80% → meest betrouwbaar
# sensor.marstek_venuse_remaining_capacity = 0.00409 kWh → buggy op V153, niet gebruiken
# sensor.marstek_venuse_available_capacity = 1.024 kWh → twijfelachtig op V153
# Gebruik ENKEL state_of_charge als primaire SoC bron

# ⚠️ CT CLAMP WAARSCHUWING:
# Als ct_connected = off: Marstek meet geen netafname
# Watchdog moet dit detecteren en gebruiker notificeren
# Zonder CT: batterij kan niet optimaal werken (weet niet wat het net doet)
```

### 6.6 HeatPumpAgent

```python
# Alle entiteiten bevestigd (2026-05-05)
READ_ENTITIES = {
    # Temperatuur
    "indoor_temp_c":    "sensor.anna_temperatuur",              # 22.0°C ✅
    "outdoor_temp_c":   "sensor.smile_anna_buitentemperatuur",  # 10.9°C ✅
    "setpoint_c":       "sensor.anna_instelpunt",               # 21.0°C ✅
    
    # HVAC status
    "hvac_action":      ("climate.anna", "hvac_action"),        # heating|cooling|idle
    "hvac_mode":        "climate.anna",                          # state: auto|heat
    "preset_mode":      ("climate.anna", "preset_mode"),        # home|asleep|away|vacation|no_frost
    "is_heating":       "binary_sensor.opentherm_verwarmen",
    "flame_active":     "binary_sensor.opentherm_vlamstatus",
    "modulation_pct":   "sensor.opentherm_modulatieniveau",
    
    # DHW (sanitair warm water)
    "dhw_temp_c":       "sensor.opentherm_sww_temperatuur",     # 46.1°C ✅
    "water_temp_c":     "sensor.opentherm_watertemperatuur",    # 49.3°C
    "return_temp_c":    "sensor.opentherm_retourtemperatuur",
    "ssw_modus":        "select.opentherm_ssw_modus",            # off|boost|auto|comfort|eco
    "dhw_setpoint_c":   "number.opentherm_instelpunt_sanitair_warm_water",
    
    # Aanwezigheid proxy
    "licht_lx":         "sensor.anna_licht",                    # 60.2 lx — occupancy proxy ✅
}

WRITE_SERVICES = {
    "set_setpoint":  ("climate", "set_temperature", "climate.anna"),
    "set_mode":      ("climate", "set_hvac_mode", "climate.anna"),
    # Preset voor vakantie — gebruik Anna's ingebouwde vacation preset
    "set_preset":    ("climate", "set_preset_mode", "climate.anna"),
    # DHW via select — beter dan aan/uit switch
    "set_dhw":       ("select", "select_option", "select.opentherm_ssw_modus"),
}

# Preset modus mapping:
PRESET_MODES = {
    "home":      "home",      # Normaal thuis
    "asleep":    "asleep",    # Nachtmodus
    "away":      "away",      # Weg (korte afwezigheid)
    "vacation":  "vacation",  # Vakantie — Anna beheert vorstbescherming zelf
    "no_frost":  "no_frost",  # Enkel vorstbescherming
}

# DHW modus mapping:
DHW_MODES = {
    "solar_boost": "boost",    # Zonne-energie trigger: onmiddellijk opwarmen
    "normal":      "comfort",  # Normaal overdag
    "night":       "eco",      # Nacht (AUTO 2) — lagere setpoint
    "vacation":    "off",      # Vakantie — geen DHW nodig
}

# ⚠️ KOELING — STATUS ONBEKEND, TE TESTEN BIJ WARM WEER:
# Zie sectie 14.2 voor volledige details en testprocedure.
# COOLING_ENABLED=False (huidige instelling) — geen koelingsacties van het brein.
# Test bij eerste warme dag (>22°C): hvac_mode='auto', setpoint=18°C, observeer hvac_action.
# Als hvac_action='cooling' verschijnt: koeling werkt zonder Thermastage feature → activeer.
# Als geen reactie: koeling vereist €300 Thermastage feature via Thercon Belgium.

# ⚠️ SCHEMA: select.anna_thermostaat_schema moet 'off' blijven
# EnergyBrain en Anna's ingebouwde schema mogen niet samen draaien
# CHECK: bij startup (StartupRecovery) EN elke 15 min (DayPlanner update cycle)
# Als schema actief → zet 'off' + stuur WARNING notificatie aan gebruiker

# OCCUPANCY PROXY via sensor.anna_licht:
# Donker overdag (< 20 lx) = waarschijnlijk niemand thuis of slaapkamer
# Dit is een zwak signaal — enkel als extra input voor OccupancyInferrer
# Nooit als enige basis voor een beslissing

# HardLimits altijd toegepast voor schrijven
# Max 0.5°C stap per cyclus
# Geen inmenging bij outdoor_temp < -2°C
```

### 6.7 HomeConnectAgent

```python
# ⚠️ CRITICAL: Different delay types per appliance
# Vaatwasser: StartInRelative = seconds until START   ✅ BEVESTIGD: unit=s, max=86400 (24h)
# Wasmachine:  FinishInRelative = seconds until END   ⚠️ EENHEID ONBEKEND: max=100 in unavailable staat
# Droger:      FinishInRelative = seconds until END   ⚠️ EENHEID ONBEKEND: max=100 in unavailable staat
#
# ⚠️ TODO wasmachine + droger: test delay eenheid wanneer programma actief is
# Hypothese: ook seconden (zoals vaatwasser), maar max=100 is verdacht klein → verificatie vereist

APPLIANCE_CONFIG = {
    ApplianceType.DISHWASHER: {
        # ✅ VERIFIED 2026-05-07 from HA Developer Tools → States
        # Live confirmation: start_op_afstand=on, status=ready, deur=closed, geselecteerd=eco_50
        "remote_sensor":    "binary_sensor.vaatwasser_start_op_afstand",   # on = ready for remote start ✅ confirmed live
        "remote_ready":     "on",
        "afstandsbediening":"binary_sensor.vaatwasser_afstandsbediening",  # on = remote control enabled
        "connectiviteit":   "binary_sensor.vaatwasser_connectiviteit",     # on = connected
        "power_switch":     "switch.vaatwasser_inschakelen",               # off = idle; write to start
        # ✅ GECORRIGEERD: was number.vaatwasser_start_in_relatie — entity bestaat NIET
        # Delay = seconds until START (unit: s, min:0, max:86400)
        "delay_entity":     "number.vaatwasser_begin_relatief",            # seconds until start, min:0, max:86400, unit:s
        "delay_type":       "start_relative",
        "status_sensor":    "sensor.vaatwasser_status",                    # inactive|ready|delayedstart|run|pause|actionrequired|finished|error|aborting
        "door_sensor":      "sensor.vaatwasser_deur",                      # closed|locked|open
        "active_program":   "select.vaatwasser_actieve_programma",         # dishcare_dishwasher_program_*
        "selected_program": "select.vaatwasser_geselecteerd_programma",    # intensiv_70|auto_2|eco_50|quick_45|pre_rinse|night_wash|kurz_60|machine_care
        "program_end_time": "sensor.vaatwasser_programma_eindtijd",        # device_class: timestamp
        "program_progress": "sensor.vaatwasser_programma_voortgang",       # unit: %
        "stop_button":      "button.vaatwasser_stop_programma",
        # Wash options (read/write, not needed for scheduling but available)
        "brilliant_dry":    "switch.vaatwasser_briljant_droog",            # off by default
        "extra_quiet":      "switch.vaatwasser_extra_stille_modus",
        "intensive_zone":   "switch.vaatwasser_intensieve_zone",           # off by default
        "vario_speed":      "switch.vaatwasser_vario_speed",               # off by default
        "interior_light":   "binary_sensor.vaatwasser_interior_illumination_active",
        "typical_power_w":  1800,
        "typical_duration": 2.0,
    },
    ApplianceType.WASHING_MACHINE: {
        # ✅ VERIFIED 2026-05-07 from HA Developer Tools → States
        "remote_sensor":    "binary_sensor.wasmachine_start_op_afstand",   # unavailable when inactive; on = ready for remote start
        "remote_ready":     "on",
        "afstandsbediening":"binary_sensor.wasmachine_afstandsbediening",  # remote control enabled
        "lokale_controle":  "binary_sensor.wasmachine_lokale_controle",    # off = remote OK
        "connectiviteit":   "binary_sensor.wasmachine_connectiviteit",     # on = connected
        "power_switch":     "switch.wasmachine_inschakelen",               # off = idle; write to start
        # ✅ GECORRIGEERD: was number.wasmachine_finish_in_relative — entity bestaat NIET
        "delay_entity":     "number.wasmachine_relatieve_eindtijd",        # min:0, max:100 (eenheid onbekend bij unavailable)
        "delay_type":       "finish_relative",
        "status_sensor":    "sensor.wasmachine_status",                    # inactive|ready|delayedstart|run|pause|actionrequired|finished|error|aborting
        "door_sensor":      "sensor.wasmachine_deur",                      # closed|locked|open
        "active_program":   "select.wasmachine_actieve_programma",         # laundry_care_washer_program_*
        "selected_program": "select.wasmachine_geselecteerd_programma",    # same options as active_program
        "spin_speed":       "select.wasmachine_centrifuge_snelheid",       # off|400|600|700|800|900|1000|1200|1400|1600 rpm|ul_off|ul_low|ul_medium|ul_high
        "temperature":      "select.wasmachine_temperatuur",               # cold|20|30|40|50|60|70|80|90°C|ul_cold|ul_warm|ul_hot|ul_extra_hot
        "program_end_time": "sensor.wasmachine_programma_eindtijd",        # device_class: timestamp
        "program_progress": "sensor.wasmachine_programma_voortgang",       # unit: %
        "pause_button":     "button.wasmachine_pauzeer_programma",
        "stop_button":      "button.wasmachine_stop_programma",
        "resume_button":    "button.wasmachine_vervolg_programma",
        "child_lock":       "switch.wasmachine_kinderslot",
        # i-Dos auto-dosing (not used by EnergyBrain, read-only reference)
        "i_dos_1_active":   "switch.wasmachine_i_dos_1_active",
        "i_dos_2_active":   "switch.wasmachine_i_dos_2_active",
        "i_dos_1_level":    "number.wasmachine_i_dos_1_basisniveau",       # mL, min:5, max:200
        "i_dos_2_level":    "number.wasmachine_i_dos_2_basisniveau",       # mL, min:5, max:200
        "typical_power_w":  2000,
        "typical_duration": 2.5,
    },
    ApplianceType.DRYER: {
        # ✅ GECORRIGEERD: start_op_afstand bestaat wel, lokale_controle was fout
        "remote_sensor":    "binary_sensor.droger_start_op_afstand",
        "remote_ready":     "on",               # on = klaar voor remote start
        "afstandsbediening":"binary_sensor.droger_afstandsbediening",  # on = remote enabled
        "power_switch":     "switch.droger_inschakelen",
        "delay_entity":     "number.droger_relatieve_eindtijd",
        "delay_type":       "finish_relative",
        # ⚠️ DELAY EENHEID ONDUIDELIJK: max=100 in unavailable staat, max=86400s in actieve staat
        # Test in praktijk: wat is de eenheid wanneer programma geselecteerd is?
        "status_sensor":    "sensor.droger_status",   # inactive|ready|delayedstart|run|pause|finished|error
        "door_sensor":      "sensor.droger_deur",     # closed|locked|open
        "active_program":   "select.droger_actieve_programma",
        "selected_program": "select.droger_geselecteerd_programma",
        "drying_target":    "select.droger_droogdoel",  # iron_dry|gentle_dry|cupboard_dry|cupboard_dry_plus|extra_dry
        "stop_button":      "button.droger_stop_programma",
        "typical_power_w":  2500,
        "typical_duration": 3.5,
    },
}
```

---

### 6.8 HAControlAgent (agents/ha_control_agent.py)

**Doel:** Leest alle EnergyBrain input helpers uit HA. Dit is de brug tussen de HA besturingsinterface en het brein.

```python
# HA Input Helper entities — aangemaakt via packages/energybrain_control.yaml
# EnergyBrain leest deze elke cyclus via HAClient

CONTROL_ENTITIES = {
    # --- Systeem ---
    "brain_enabled":        "input_boolean.energybrain_enabled",
    "brain_mode":           "input_select.energybrain_mode",

    # --- Vakantie ---
    "vacation_active":      "input_boolean.energybrain_vacation_mode",
    "vacation_start":       "input_datetime.energybrain_vacation_start",
    "vacation_end":         "input_datetime.energybrain_vacation_end",

    # --- DHW ---
    "dhw_boost_now":        "input_boolean.energybrain_dhw_boost_now",
    "dhw_target_temp":      "input_number.energybrain_dhw_target_temp",

    # --- Status terug naar HA (EnergyBrain schrijft deze) ---
    "status_text":          "input_text.energybrain_status",
    "last_action":          "input_text.energybrain_last_action",
    "today_plan":           "input_text.energybrain_today_plan",
    "next_action":          "input_text.energybrain_next_action",
}

class HAControlAgent(BaseAgent):

    async def get_control_state(self) -> ControlState:
        """
        Leest alle input helpers in één batch.
        Geeft ControlState terug die DayPlanner gebruikt.
        Elke beslissingscyclus aangeroepen.
        """

    async def update_status(
        self,
        status: str,
        last_action: str,
        today_plan: str,
        next_action: str,
    ) -> None:
        """
        Schrijft EnergyBrain status terug naar HA input_text helpers.
        Zichtbaar op het HA dashboard als leesbare tekst.
        Aangeroepen na elke cyclus.
        """
```

---

## 7. Orchestrator (orchestrator/orchestrator.py)

```python
class Orchestrator:
    """
    Main control loop. Three concurrent asyncio tasks.
    
    Task 1 — Realtime (every 60 seconds):
        1. Collect SystemState from all agents
        2. Validate hard limits on any pending actions
        3. Execute due tasks from current DayPlan
        4. Check and enforce appliance deadlines
        5. Persist state to DB
        6. Queue notifications
    
    Task 2 — Day planner (every 15 minutes):
        1. Update DayPlan if PV forecast changed > 10%
        2. Recalculate scheduled task timing
        3. Update SystemState.day_plan
    
    Task 3 — Watchdog (every 5 minutes, independent):
        1. Check all safety conditions
        2. Execute safety actions directly (bypass brain)
        3. Send SAFETY_ALARM if needed
    
    Scheduled jobs (sequential logic — each job depends on previous):
        21:00 daily:   PVForecaster.update_calibration() + OutcomeTracker.log_outcome(pv)
        21:30 daily:   daily summary notification + BatteryDispatcher stub log
        02:00 daily:   OutcomeTracker.check_drift() → notify if drift detected
        02:00 daily:   ThermalModel.update_model() (train on yesterday's system_states)
        02:30 daily:   WeekStrategist.calculate_strategy() (needs updated ThermalModel)
        02:00 Sunday:  PatternLearner.update_patterns() (weekly, uses OutcomeTracker feedback)
        03:00 daily:   aggregate_to_hourly() + cleanup_old_data() (DB maintenance)
        06:30 daily:   DayPlanner.create_day_plan() + BatteryDispatcher.calculate_dispatch_plan()
        06:30 daily:   morning notification (solar + battery stub plan)
        07:00 Monday:  Week strategy notification
        07:00 1st:     OutcomeTracker.get_accuracy_report() → MONTHLY_REPORT notification
        Every hour:    BatteryDispatcher.execute_plan() (STUB: logs only)
    """
    
    REALTIME_INTERVAL = 60
    DAYPLAN_INTERVAL = 15 * 60
    WATCHDOG_INTERVAL = 5 * 60
```

---

## 8. Database Schema (SQLite, gelaagde opslag)

```
Opslag strategie — niet alles even lang bewaren:

Laag 1 — Volledige resolutie (60 sec):   90 dagen rolling
           → Recente modeltraining, debugging

Laag 2 — Uurlijkse aggregaten:            2 jaar
           → Seizoenspatronen vergelijken
           → "Vorige januari vs deze januari"

Laag 3 — Dagsamenvattingen:               Onbeperkt (klein!)
           → Jaar-op-jaar vergelijking

Laag 4 — Geleerde modelparameters:        Onbeperkt
           → Nooit verwijderen — dit IS het geheugen
           → Per seizoen (Q1/Q2/Q3/Q4) aparte snapshots
```

```sql
-- LAAG 1: Volledige resolutie (90 dagen rolling)
CREATE TABLE system_states (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    pv_power_w REAL,
    pv_daily_kwh REAL,
    battery_soc_pct REAL,
    battery_power_w REAL,
    grid_power_w REAL,
    indoor_temp_c REAL,
    outdoor_temp_c REAL,
    hvac_setpoint_c REAL,
    hvac_mode TEXT,
    hvac_regime TEXT,           -- heating|cooling|idle (for ThermalModel)
    dhw_boost_active INTEGER,
    dhw_temp_c REAL,            -- NULL if sensor not available
    baseline_power_w REAL,      -- P1 minus known large loads (occupancy inference)
    occupancy_type TEXT,        -- empty|normal|school_holiday|unknown
    dishwasher_running INTEGER,
    washing_machine_running INTEGER,
    dryer_running INTEGER
);

-- LAAG 2: Uurlijkse aggregaten (2 jaar)
CREATE TABLE hourly_aggregates (
    id INTEGER PRIMARY KEY,
    hour_start TEXT NOT NULL,   -- ISO datetime, truncated to hour
    season TEXT,                -- Q1/Q2/Q3/Q4
    weekday INTEGER,            -- 0=Mon, 6=Sun
    occupancy_type TEXT,
    avg_pv_power_w REAL,
    avg_grid_power_w REAL,
    avg_indoor_temp_c REAL,
    avg_outdoor_temp_c REAL,
    avg_baseline_power_w REAL,
    hvac_mode TEXT,
    dhw_boost_active INTEGER,
    appliances_running TEXT      -- JSON list
);

-- LAAG 3: Dagsamenvattingen (onbeperkt)
CREATE TABLE daily_summaries (
    id INTEGER PRIMARY KEY,
    date TEXT NOT NULL UNIQUE,
    season TEXT,
    pv_total_kwh REAL,
    grid_import_kwh REAL,
    grid_export_kwh REAL,
    self_use_pct REAL,
    peak_demand_kw REAL,        -- Highest 15-min average (capacity tariff)
    dhw_boost_count INTEGER,
    appliances_started TEXT,    -- JSON
    vacation_day INTEGER,       -- 1 if vacation mode was active
    estimated_savings_eur REAL
);

-- LAAG 4: Geleerde modelparameters (nooit verwijderen)
CREATE TABLE thermal_model_snapshots (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    season TEXT,                -- Q1/Q2/Q3/Q4 — separate model per season
    occupancy_type TEXT,        -- normal|school_holiday
    cooling_rate REAL,
    heating_rate REAL,
    thermal_mass_hours REAL,
    r2_score REAL,
    samples_count INTEGER,
    model_type TEXT,            -- "linear" | "gradient_boosting"
    model_blob BLOB             -- Serialized sklearn model (joblib)
);

CREATE TABLE pv_calibration_factors (
    id INTEGER PRIMARY KEY,
    updated_at TEXT,
    season TEXT,                -- Q1/Q2/Q3/Q4 (kept for backward compat)
    cloud_category TEXT,        -- clear|partial|overcast (kept for backward compat)
    calibration_factor REAL,    -- actual/predicted ratio
    sample_count INTEGER,
    ridge_model_blob BLOB       -- Serialized Ridge model (joblib)
);

CREATE TABLE pattern_learner_models (
    id INTEGER PRIMARY KEY,
    updated_at TEXT,
    model_name TEXT,            -- dhw|dishwasher|washing|dryer|cooking
    occupancy_type TEXT,        -- normal|school_holiday (Layer 3)
    samples_count INTEGER,
    accuracy_pct REAL,          -- From OutcomeTracker
    feature_importances TEXT,   -- JSON: {"outdoor_temp_c": 0.34, ...}
    model_blob BLOB             -- Serialized GradientBoosting model (joblib)
);

-- Legacy table — kept for backward compat with old probability lookup
CREATE TABLE usage_patterns (
    id INTEGER PRIMARY KEY,
    updated_at TEXT,
    pattern_type TEXT,
    appliance_type TEXT,
    weekday INTEGER,
    hour INTEGER,
    season TEXT,
    occupancy_type TEXT,
    probability REAL,
    sample_count INTEGER
);

-- ⭐ OutcomeTracker — feedback loop voor alle modellen
CREATE TABLE predictions (
    id INTEGER PRIMARY KEY,
    prediction_id TEXT UNIQUE,  -- UUID linking prediction to outcome
    model_name TEXT,            -- dhw_demand|appliance_loading|cooking_peak|pv_forecast
    features TEXT,              -- JSON dict of feature values
    predicted_value REAL,
    predicted_at TEXT,
    actual_value REAL,          -- NULL until outcome observed
    outcome_at TEXT,            -- NULL until outcome observed
    is_correct INTEGER,         -- NULL until evaluated
    weight REAL DEFAULT 1.0     -- Sample weight for retraining
);

CREATE TABLE accuracy_reports (
    id INTEGER PRIMARY KEY,
    period_start TEXT,
    period_end TEXT,
    dhw_accuracy_pct REAL,
    appliance_loading_accuracy_pct REAL,
    pv_forecast_accuracy_pct REAL,
    cooking_peak_accuracy_pct REAL,
    drift_detected TEXT,        -- JSON: {"dhw_demand": false, ...}
    total_predictions INTEGER,
    estimated_savings_eur REAL,
    feature_importances TEXT,   -- JSON
    generated_at TEXT,
    notification_sent INTEGER
);

-- ⭐ BatteryDispatcher — MPC plans (STUB tot RS485 adapter)
CREATE TABLE battery_dispatch_plans (
    id INTEGER PRIMARY KEY,
    date TEXT,
    generated_at TEXT,
    hourly_target_w TEXT,       -- JSON list of 96 values (15-min intervals)
    expected_savings_eur REAL,
    peak_prevention_kw REAL,
    is_stub INTEGER,            -- 1 = stub mode (logged only, not executed)
    actual_soc_path TEXT        -- JSON list — actual SoC at each timestep (post-hoc)
);

-- Actions (alle, geen vervaldatum voor audit trail)
CREATE TABLE actions_taken (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL,
    action_type TEXT,
    target_entity TEXT,
    parameters TEXT,
    is_stub INTEGER,
    success INTEGER,
    verified INTEGER,           -- 1 if start_verifier confirmed running
    retry_needed INTEGER,       -- 1 if first attempt failed
    reason TEXT,
    horizon TEXT
);

-- Appliance deadline tracking
CREATE TABLE appliance_waiting (
    id INTEGER PRIMARY KEY,
    appliance_type TEXT,
    waiting_since TEXT,
    started_at TEXT,
    is_force_started INTEGER,
    actual_wait_hours REAL,
    surplus_at_start_w REAL,
    cooking_peak_delayed INTEGER  -- 1 if delayed due to cooking peak
);

-- Capacity tariff tracking (per month)
CREATE TABLE capacity_tariff_events (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    peak_kw REAL,
    duration_minutes INTEGER,
    caused_by TEXT,             -- JSON list of active appliances
    month TEXT                  -- YYYY-MM
);

-- Vakantieperiodes (uitgesloten van leermodules)
CREATE TABLE vacation_periods (
    id INTEGER PRIMARY KEY,
    start_date TEXT,
    end_date TEXT,
    noted_at TEXT
);

-- Safety events
CREATE TABLE safety_events (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    event_type TEXT,
    severity TEXT,
    message TEXT,
    action_taken TEXT,
    notification_sent INTEGER
);

-- Systeem heartbeat (voor downtime detectie)
CREATE TABLE system_heartbeat (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL     -- Written every 60 seconds
);

-- Dagplannen archief
CREATE TABLE day_plans (
    id INTEGER PRIMARY KEY,
    date TEXT,
    generated_at TEXT,
    total_pv_forecast_kwh REAL,
    actual_pv_kwh REAL,
    surplus_windows TEXT,
    scheduled_tasks TEXT,
    week_strategy_note TEXT
);
```

**Aggregatie jobs:**
```python
# Elke nacht om 03:30 — na model updates
async def aggregate_to_hourly():
    """Move yesterday's 60-sec data into hourly_aggregates."""
    # Keep 90-day rolling window in system_states
    # Keep 2-year window in hourly_aggregates
    # daily_summaries: never delete

async def cleanup_old_data():
    """Remove system_states older than 90 days."""
    """Remove hourly_aggregates older than 2 years."""
    """Never touch thermal_model_snapshots, pv_calibration_factors, usage_patterns."""
```

---

## 9. .env.template

```bash
# EnergyBrain Configuration — copy to .env, fill in values, NEVER commit .env

# Home Assistant
HA_URL=http://192.168.68.62:8123
HA_TOKEN=your_long_lived_access_token_here

# Push notifications — HA Companion app
# Find target: HA → Settings → Mobile App → Notifications → target name
NOTIFICATION_DEVICE=your_mobile_device_name_here

# Marstek write control
# false = V153 firmware write-bug actief (RS485 adapter aanwezig, schrijven geblokkeerd)
# true  = pas activeren na V154 firmware update en succesvolle write-test
MARSTEK_WRITE_ENABLED=false

# Energy pricing (Belgium)
ENTSOE_API_KEY=your_entso_e_api_key_here
STATIC_IMPORT_PRICE_EUR_KWH=0.25
STATIC_EXPORT_PRICE_EUR_KWH=0.036
# Capacity tariff: ~€47.50/kW/year excl. BTW (2026, Fluvius). Verify annually.
# Source: https://www.fluvius.be/nl/nettarieven-elektriciteit-en-aardgas
CAPACITY_TARIFF_EUR_KW_YEAR=47.50

# Location (Korbeek-lo, Bierbeek)
LATITUDE=50.8597
LONGITUDE=4.7628
TIMEZONE=Europe/Brussels

# Surplus thresholds (Watts) — static values
# Minimum net surplus required before EnergyBrain considers starting each load.
# Static is fine: these represent minimum viable surplus to run the appliance
# without drawing significantly from grid. PV calibration handles day-quality variation.
SURPLUS_DHW_W=2000
SURPLUS_DISHWASHER_W=1800
SURPLUS_WASHING_MACHINE_W=2000
SURPLUS_DRYER_W=2500
SURPLUS_BATTERY_W=500          # Minimum surplus before battery is considered charged (read-only)
SURPLUS_HVAC_W=1500            # Minimum surplus before HVAC preloading is considered

# Battery safety
BATTERY_SOC_MIN_PCT=10
# Below thresholds define minimum battery SOC before EnergyBrain triggers appliances/DHW
# using grid power (not waiting for solar surplus).
# ⚠️ Currently reserved for future use pending V154 firmware fix (Marstek RS485 write-bug).
# When Marstek write is enabled: don't discharge below these thresholds for non-safety loads.
BATTERY_SOC_DHW_MIN_PCT=50
BATTERY_SOC_APPLIANCE_MIN_PCT=70

# HVAC safety
HVAC_MAX_SETPOINT_C=22.5
HVAC_MIN_SETPOINT_C=16.0
HVAC_MAX_STEP_PER_CYCLE_C=0.5
HVAC_FROST_OUTDOOR_C=-2.0
INDOOR_TEMP_MIN_WINTER_C=17.0

# Appliance deadlines
DISHWASHER_MAX_WAIT_H=4
DISHWASHER_HARD_DEADLINE=20:00
WASHING_MACHINE_MAX_WAIT_H=6
WASHING_MACHINE_HARD_DEADLINE=20:00
DRYER_MAX_WAIT_H=8
DRYER_HARD_DEADLINE=21:00

# Intelligence activation thresholds
THERMAL_MODEL_MIN_SAMPLES=336
PATTERN_LEARNER_MIN_DAYS_BASIC=14         # Layer 1: weekday patterns active
PATTERN_LEARNER_MIN_DAYS_SEASONAL=90      # Layer 2: season-aware patterns active
PATTERN_LEARNER_MIN_DAYS_YEARLY=365       # Layer 3: school holiday patterns active
PV_CALIBRATION_MIN_DAYS=30
OSCILLATION_SWITCH_THRESHOLD=3
THERMAL_R2_UPGRADE_THRESHOLD=0.85         # Below this after 90d → switch to GBR

# OutcomeTracker (feedback loop & drift detection)
DRIFT_THRESHOLD_PCT=15.0                  # Accuracy drop that triggers drift alert
DRIFT_WINDOW_DAYS=14                      # Rolling window for drift detection
ACCURACY_BASELINE_DAYS=30                 # Baseline period for comparison

# BatteryDispatcher (MPC — STUB tot V154 firmware fix)
BATTERY_MPC_HORIZON_HOURS=24
BATTERY_MPC_TIMESTEP_MIN=15
BATTERY_MAX_SOC_PCT=95
BATTERY_DISPATCH_LOG_HOURLY=true          # Log STUB plan every hour

# System
CYCLE_INTERVAL_S=60
WATCHDOG_INTERVAL_S=300
LOG_LEVEL=INFO
DB_PATH=/homeassistant/energybrain/energybrain.db
DB_RETENTION_DAYS=90
DB_HOURLY_RETENTION_YEARS=2

# Contract type
CONTRACT_TYPE=static            # static (current) or dynamic (ENTSO-E)
CHEAP_HOUR_THRESHOLD_PCT=70
EXPENSIVE_HOUR_THRESHOLD_PCT=130

# Capacity tariff protection — DEFAULT cooking peak window
# Used as fallback before PatternLearner is trained (< 14 days).
# After training: dynamic per (weekday × weather × school holiday) from GBC model.
COOKING_PEAK_START_DEFAULT=17:00
COOKING_PEAK_END_DEFAULT=18:30
MIN_GAP_BETWEEN_STARTS_MIN=15

# Start verification
START_VERIFY_DELAY_S=60
START_RETRY_DELAY_S=120

# DHW
DHW_TARGET_TEMP_C=55.0          # Target boiler temp for evening shower peak

# Vacation mode — via Anna's ingebouwde vacation preset (geen aparte entity nodig)
# EnergyBrain detecteert automatisch als climate.anna preset_mode = 'vacation'
```

---

## 10. pyproject.toml

```toml
[project]
name = "energybrain"
version = "0.3.0"
description = "Intelligent learning home energy management system"
requires-python = ">=3.11"

dependencies = [
    "aiohttp>=3.9",
    "python-dotenv>=1.0",
    "pymodbus>=3.6",
    "aiosqlite>=0.20",
    "scikit-learn>=1.4",      # ThermalModel, PVForecaster, PatternLearner
    "scipy>=1.12",            # BatteryDispatcher MPC (linprog)
    "numpy>=1.26",
    "pandas>=2.2",
    "joblib>=1.3",            # Persist ML models to disk
    "structlog>=24.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "pytest-mock>=3.12",
    "aioresponses>=0.7",
    "coverage>=7.0",
    "freezegun>=1.4",
]
# ML upgrade path — only install after 365+ days of data and OutcomeTracker recommendation
ml-advanced = [
    "xgboost>=2.0",           # Faster than GBC, better for >365 samples
    "lightgbm>=4.3",           # Alternative to xgboost
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.coverage.run]
source = ["energybrain"]
omit = ["*/tests/*", "scripts/*"]
```

---

## 11. Implementatiefasen

### Fase 0 — Fundament
```
Maak alle mappen en __init__.py bestanden
Schrijf: pyproject.toml, .env.template, .gitignore
Schrijf: models.py (volledig)
Schrijf: exceptions.py, config.py
Schrijf: utils/logging_config.py, utils/retry.py, utils/ha_client.py
Schrijf: persistence/database.py (schema + migraties)

→ TEST: python -c "from energybrain.models import SystemState; print('OK')"
```

### Fase 1 — Data Agents
```
Schrijf + test elk agent:
base_agent → ha_agent → goodwe_agent → p1_agent → marstek_agent (stub)
→ heat_pump_agent → home_connect_agent → weather_agent
→ energy_price_agent → notification_agent

→ TEST: pytest tests/test_agents/ -v --cov=energybrain/agents
→ Coverage minimum 80% per agent
```

### Fase 2 — Safety
```
Schrijf + test: hard_limits.py, watchdog.py, rollback.py

Kritieke tests:
- hard_limits blokkeert setpoint > 22.5°C
- watchdog werkt onafhankelijk van brain
- marstek stub nooit schrijft als MARSTEK_WRITE_ENABLED=false

→ TEST: pytest tests/test_safety/ -v
```

### Fase 3 — Intelligence
```
Schrijf + test (in volgorde — afhankelijkheden):
1. oscillation_detector  (geen dependencies)
2. thermal_model         (5 features: outdoor + solar + wind + hour + hvac_active)
3. pv_forecaster         (Ridge regression op 5 continue features)
4. pattern_learner       (5× GradientBoosting modellen, 9 features)
5. outcome_tracker       (feedback loop — gebruikt door alle bovenstaande)
6. battery_dispatcher    (MPC via scipy.optimize.linprog, STUB mode)
7. day_planner           (gebruikt thermal + pv + pattern + outcome)
8. week_strategist       (gebruikt thermal + pv + oscillation)

Kritieke tests per module:
thermal_model:      fallback bij < 336 samples, 5-feature voorspelling, GBR upgrade trigger
pv_forecaster:      Ridge model training, surplus window detectie, POA berekening
pattern_learner:    GBC training met 9 features, fallback naar defaults bij < 14 dagen
outcome_tracker:    log_prediction + log_outcome, drift detectie, accuracy report
battery_dispatcher: MPC oplossing, stub mode logging, SoC pad simulatie
oscillation:        detectie na 3 switches, freeze periode
day_planner:        zonnige dag, bewolkte dag (alleen deadlines), ochtendnotificatie
week_strategist:    koeling weigeren bij korte warmteperiode, oscillatie override

→ TEST: pytest tests/test_intelligence/ -v --cov=energybrain/intelligence
```

### Fase 4 — Orchestrator & Integratie
```
Schrijf: orchestrator.py, main.py
Schrijf: scripts/ (check_ha_connection, check_all_agents, simulate_day, train_models)

Integratie tests:
test_full_cycle.py:        complete 06:00-23:00 simulatie
test_day_planning.py:      zonnige dag, bewolkte dag, halve dag zon
test_week_strategy.py:     oscillatie scenario, koude periode, stabiel
test_safety_scenarios.py:  watchdog ingrijpen, hard limit blok, rollback

→ TEST: pytest tests/ -v --tb=short
→ COVERAGE: >= 80% totaal
→ TEST: python scripts/check_ha_connection.py (echte HA vereist)
```

### Fase 5 — Documentatie
```
README.md: installatie, configuratie, starten, intelligentie-ontwikkeling, beperkingen
Type hints op alle publieke methodes
Google-stijl docstrings op alle klassen

→ FINAL: pytest tests/ -v && python scripts/check_all_agents.py
```

---

## 12. Kritieke Test Scenarios

```python
# Day planning
test_sunny_day_appliances_start_in_surplus_window()
test_cloudy_day_all_appliances_start_at_deadline()
test_partial_sun_dhw_priority_over_appliances()
test_wednesday_forced_dhw_boost_at_13h()
test_morning_notification_sent_when_min_1_5h_surplus()
test_no_morning_notification_when_too_little_sun()

# Intelligence
test_thermal_model_prevents_unnecessary_heating_after_cold_spell()
test_oscillation_detected_and_strategy_frozen()
test_week_strategy_refuses_cooling_for_2day_warm_spell()
test_pv_calibration_factor_applied_after_30_days()
test_pattern_learner_predicts_wednesday_dhw_demand()
test_fallback_defaults_used_before_min_samples()

# Safety
test_hard_limit_blocks_setpoint_above_22_5()
test_watchdog_fires_when_indoor_below_17()
test_watchdog_fires_when_dhw_below_40_after_17h()
test_marstek_stub_never_writes_when_disabled()
test_rollback_executes_after_configured_timeout()
test_safety_alarm_notification_never_throttled()

# Notifications
test_morning_notification_includes_ready_appliances_only()
test_force_start_notification_sent_with_wait_duration()
test_daily_summary_sent_at_21h30()
test_week_strategy_notification_sent_monday_morning()
test_no_duplicate_notification_per_type_per_day()
```

---

## 13. Logging Standaard

```python
# Beslissing van het brein:
logger.info("decision",
    horizon="realtime",
    action_type="start_appliance",
    target="washing_machine",
    reason="surplus_window 11:00-15:30, remote_start=True, surplus=2840W",
    surplus_w=2840, battery_soc=76, is_stub=False
)

# Intelligence update:
logger.info("model_update",
    model="thermal_model",
    r2_score=0.87, mae_c=0.3,
    samples=1440, cooling_rate=0.38, heating_rate=0.14
)

# Safety event:
logger.warning("safety_triggered",
    limit="indoor_temp_min_winter",
    current=16.8, threshold=17.0,
    action="force_heat", notification_sent=True
)

# Stub:
logger.info("stub_action",
    action_type="set_battery_mode",
    intended="Manual",
    reason="MARSTEK_WRITE_ENABLED=false"
)

# Deadline force start:
logger.info("deadline_force_start",
    appliance="washing_machine",
    waited_hours=6.2,
    reason="max_wait_hours exceeded",
    surplus_w=0, notification_sent=True
)
```

## 14. Kritieke Aanvullingen

### 14.1 Vakantie Modus

```python
class VacationMode:
    """
    Pauses learning modules during absence to prevent data pollution.
    
    When ACTIVE:
    - PatternLearner.update_patterns() → skipped
    - ThermalModel.update_model() → skipped
    - PVForecaster.update_calibration() → still runs (PV unaffected by occupancy)
    - Safety watchdog → still runs (protect house regardless)
    - DHW boost → disabled (nobody home = no hot water needed)
    - HVAC → frost protection only (hard limit 16°C minimum)
    - Appliance automation → disabled
    - Daily summary notification → replaced by "Vakantie modus actief"
    
    Activation:
    - Via HA climate.anna preset_mode = 'vacation' (Anna's ingebouwde preset)
    - Geen aparte input_boolean nodig — Anna beheert vorstbescherming zelf
    - EnergyBrain leest preset_mode van climate.anna om status te kennen
    
    On activation:
    - Set climate.anna preset 'vacation'
    - Set select.opentherm_ssw_modus to 'off'
    - Pause PatternLearner and ThermalModel training
    - Send notification: "🏖️ Vakantie modus actief"
    
    On deactivation:
    - Set climate.anna preset 'home'
    - Set select.opentherm_ssw_modus to 'comfort'
    - Resume learning (exclude vacation period from training)
    - Rebuild day plan immediately
    - Send notification: "🏠 Welkom terug! EnergyBrain hervat normaal."
    """
    
    VACATION_PRESET = "vacation"
    HOME_PRESET = "home"
    ANNA_ENTITY = "climate.anna"
    
    def is_active(self) -> bool:
        """Check HA entity state."""
    
    def mark_vacation_period(self, start: datetime, end: datetime) -> None:
        """
        Store vacation period in DB so PatternLearner can exclude it
        from future training runs.
        """
```

**DB schema:** zie sectie 8 — `vacation_periods` tabel is al gedefinieerd in het hoofdschema.

---

### 14.2 HVAC Modus Detectie — Bevestigd

```python
# Bevestigde entiteiten (2026-05-05):
HVAC_ENTITIES = {
    # Lezen
    "hvac_action":    ("climate.anna", "hvac_action"),    # heating|cooling|idle
    "hvac_mode":      "climate.anna",                      # state: auto|heat
    "is_heating":     "binary_sensor.opentherm_verwarmen", # True = actief aan het verwarmen
    "flame_active":   "binary_sensor.opentherm_vlamstatus", # True = brander aan
    "modulation":     "sensor.opentherm_modulatieniveau",  # % brander modulation
    
    # Schrijven
    "set_mode":       ("climate", "set_hvac_mode", "climate.anna"),
    "set_setpoint":   ("climate", "set_temperature", "climate.anna"),
}

# ⚠️ COOLING — STATUS ONBEKEND, TE TESTEN BIJ WARM WEER:
#
# Wat zeker is:
# - Thermastage feature (€300 via Thercon) voegt koelknop toe aan Anna display
# - Zonder feature: geen fysieke koelknop op display
# - HA Plugwise integratie ondersteunt GEEN expliciete 'cool' hvac_mode voor Anna
#
# Wat ONBEKEND is:
# - Stuurt Anna in 'auto' modus OpenTherm cooling commando's naar de pomp als
#   setpoint < kamertemperatuur? Niet gedocumenteerd door Plugwise.
#
# TE TESTEN (eerste warme dag, buitentemperatuur > 22°C):
#   1. Zet climate.anna op hvac_mode 'auto' via HA
#   2. Zet setpoint naar 18°C (ruim onder kamertemperatuur)
#   3. Observeer: wordt hvac_action → 'cooling'? Reageert de pomp fysiek?
#   JA → cooling werkt via auto+lage setpoint, WeekStrategist kan dit inzetten
#   NEE → cooling onmogelijk zonder €300 Thermastage feature
#
# HUIDIGE STAAT (te koud om te testen):
# COOLING_ENABLED = False  →  WeekStrategist.cooling_days = altijd []
# Zomerhitte passief afgehandeld door thermische massa.
# Na positieve test: zet COOLING_ENABLED = True en activeer WeekStrategist koelingslogica.

# WeekStrategist (momenteel):
# - heating_days → verhoog setpoint naar 21.5°C in heat/auto modus
# - neutral_days → setpoint ongewijzigd
# - hvac_action toont achteraf: heating|idle  (|cooling na positieve test)

# ThermalModel gebruikt hvac_action:
# - 'heating' → heating regime parameters
# - 'cooling' → cooling parameters (enkel na positieve test, anders nooit gezien)
# - 'idle'    → natural drift rate

# ⚠️ VORSTBESCHERMING PRIORITEITSREGEL:
# HardLimit indoor_temp_min_winter_c=17°C OVERSCHRIJFT outdoor_frost_threshold_c=-2°C
# Reden: outdoor_frost_threshold beschermt de warmtepomp bij extreme kou (pomp kan
# vastvriezen bij lang stilstaan), MAAR als het binnen al < 17°C is, is verwarmen
# absoluut noodzakelijk ongeacht buitentemperatuur.
# Prioriteitsvolgorde: indoor_min (veiligheid bewoners) > frost_threshold (pomp)
# Implementatie in HardLimits.validate_action():
#   if indoor_temp < 17°C AND action == force_heat: ALLOW (override frost rule)
#   if outdoor_temp < -2°C AND action != force_heat: BLOCK (protect pump)
```

---

### 14.3 Bezettingsinferentie uit P1 Data

```python
class OccupancyInferrer:
    """
    Infers house occupancy from P1 baseline power consumption.
    No presence detection hardware needed.
    
    VEREENVOUDIGD: Er is bijna altijd iemand thuis (3 dagen thuiswerk +
    vrouw vaak de andere 2 dagen). Standaard aanname = bezet.
    OccupancyInferrer dient enkel om schoolvakanties te detecteren
    voor PatternLearner — niet voor complexe aanwezigheidslogica.
    
    Schoolvakantie detectie:
        - Aparte PatternLearner patronen tijdens schoolvakanties
        - "Zomervakantie zaterdag" vs "normale zaterdag" = ander patroon
        - Loaded via Flemish school calendar API at startup
    """
    
    # Belgian school calendar — loaded dynamically at startup
    # API: https://data.onderwijs.vlaanderen.be/schoolvakanties/
    SCHOOL_HOLIDAYS: dict = {}      # Filled by load_school_holidays() at startup
    
    @classmethod
    def load_school_holidays(cls, year: int) -> dict:
        """
        Fetch Flemish school holidays for given year from official API.
        Falls back to previous year data + warning if API unreachable.
        Called once at startup by Orchestrator.
        """
    
    def is_school_holiday(self, date: date) -> bool:
        """True als de dag in een schoolvakantieperiode valt."""
    
    def is_likely_occupied(self) -> bool:
        """Altijd True — er is bijna altijd iemand thuis."""
        return True
```

**PatternLearner integreert dit:**
```python
# Twee occupancy types voor PatternLearner:
#   "normal"         → standaard (ook als niemand thuis zou zijn)
#   "school_holiday" → schoolvakantie periode
# Geen "empty" type — huis is bijna altijd bezet.
```

---

### 14.4 Startup Recovery Routine

```python
class StartupRecovery:
    """
    Executed FIRST when EnergyBrain starts or restarts.
    Restores critical state from DB to avoid data loss after crash/reboot.
    
    Recovery steps (in order):
    1. Read vacation_mode from HA entity
    2. Read appliance_waiting table → restore waiting_since for each appliance
    3. Read last day_plan from DB → restore if date matches today
    4. Read last thermal_model_params → restore model without retraining
    5. Read last week_strategy → restore if generated today
    6. Check if watchdog missed any safety events during downtime
    7. Verify GoodWe operating mode = GENERAL → set if not (peak_shaving/eco ineffective)
    8. Verify Marstek operating mode:
          If MARSTEK_WRITE_ENABLED=false AND mode=Manual → send WARNING notification:
          "⚠️ Marstek staat op Manual modus — batterij is inactief. Zet op Auto in HA."
          If MARSTEK_WRITE_ENABLED=true AND mode≠Manual → set to Manual (EnergyBrain needs control)
    9. Log: "EnergyBrain restarted. Restored state from DB."
    10. Send notification if downtime > 30 minutes:
        "⚠️ EnergyBrain was {duration} offline. State hersteld."
    
    Downtime detection:
        Compare last DB timestamp with current time.
        If gap > 10 minutes: log as downtime event.
    """
    
    SIGNIFICANT_DOWNTIME_MINUTES = 30
    
    async def recover(self) -> dict:
        """Returns recovered state dict for Orchestrator."""
```

**DB toevoeging:**
```sql
CREATE TABLE system_heartbeat (
    id INTEGER PRIMARY KEY,
    timestamp TEXT NOT NULL    -- Written every 60 seconds by Orchestrator
    -- Gaps in this table = detected downtime
);
```

---

### 14.5 Capaciteitstarief & Kookpiek Beheer

```python
class CapacityTariffGuard:
    """
    Prevents unnecessary peak demand to minimize capacity tariff costs.
    
    Belgian capacity tariff (Fluvius, 2026):
        Calculated on 12-MONTH ROLLING AVERAGE of highest monthly quarter-hour peaks.
        ⚠️ NOT just the current month peak — it's the average of last 12 months.
        Rate: ~€47.50/kW/year excl. BTW (≈€57.45 incl. BTW, varies by zone).
        Verify annually: https://www.fluvius.be/nl/nettarieven-elektriciteit-en-aardgas
        Minimum: 2.5 kW always charged regardless of actual peak.
        Impact of 1 kW extra: ~€3.96/month (spread over 12 months).
    
    CapacityTariffGuard is the ONLY peak protection in this system.
    GoodWe peak_shaving mode is NOT configured (SolarGo not installed).
    
    Three rules:
    
    Rule 1 — Cooking peak protection (17:00-18:30):
        Do NOT start any new large appliance during cooking window.
        Cooking peak typically adds 1500-3000W → starting washing machine
        simultaneously could add 2000W more = significant peak.
        
    Rule 2 — Appliance staggering:
        Never start two appliances within 15 minutes of each other.
        Minimum 15-min gap between any two starts.
        Priority order determines which starts first.
    
    Rule 3 — Force-start during cooking peak:
        If deadline is exceeded AND we're in cooking peak:
        → Prefer waiting until 18:30 if hard_deadline still allows it.
        → Only force-start during cooking peak if hard_deadline < 18:45.
        This avoids creating a new monthly peak for deadline-forced starts.
    
    Note: Marstek on L1 only — cannot flatten L2/L3 peaks.
    This guard works on the demand-side instead.
    """
    
    # Cooking peak window — read from PatternLearner, not hardcoded
    # PatternLearner.get_cooking_peak(weekday, ...) returns dynamic (start, end)
    # Fallback defaults used until PatternLearner is trained (< 14 days):
    COOKING_PEAK_START_DEFAULT = time(17, 0)
    COOKING_PEAK_END_DEFAULT = time(18, 30)
    MIN_GAP_BETWEEN_STARTS_MINUTES = 15
    CAPACITY_TARIFF_EUR_KW_YEAR = 47.50   # excl. BTW, 2026 — verify annually
    
    def get_cooking_peak_window(
        self,
        pattern_learner: "PatternLearner",
        weekday: int,
        outdoor_temp_c: float,
        cloud_cover_pct: float,
        is_school_holiday: bool,
        season_q: int,
    ) -> tuple[time, time]:
        """
        Returns (start, end) of cooking peak from PatternLearner.
        Falls back to COOKING_PEAK_START/END_DEFAULT if model not ready.
        This replaces the hardcoded 17:00-18:30 window.
        """
    
    def is_cooking_peak(
        self,
        current_time: time,
        pattern_learner: "PatternLearner",
        weekday: int,
        **weather_kwargs,
    ) -> bool:
        """Returns True if in cooking protection window (dynamic or default)."""
    
    def can_start_appliance(
        self,
        appliance: ApplianceType,
        last_appliance_start: Optional[datetime],
        is_force_start: bool = False,
        hard_deadline: Optional[time] = None,
    ) -> tuple[bool, str]:
        """
        Returns (can_start, reason_if_not).
        Checks cooking peak AND 15-min stagger rule.
        
        Force-start logic:
        - If is_force_start AND in cooking peak AND hard_deadline >= 18:45:
            → Return (False, "prefer_wait_until_18:30") — delay, not block
        - If is_force_start AND in cooking peak AND hard_deadline < 18:45:
            → Return (True, "forced_despite_peak") — no choice
        - If is_force_start AND NOT in cooking peak:
            → Return (True, "forced") — normal force-start
        """
    
    def get_rolling_12month_avg_peak_kw(self) -> float:
        """
        Returns current 12-month rolling average of monthly peaks.
        Reads from capacity_tariff_events table.
        Used by DayPlanner to understand current tariff baseline.
        """
    
    def next_allowed_start(
        self, last_appliance_start: Optional[datetime]
    ) -> datetime:
        """Returns earliest datetime when next appliance can start."""
```

**DayPlanner integreert dit:**
```python
# Bij het inplannen van taken:
# 1. Check CapacityTariffGuard.can_start_appliance(is_force_start=False)
# 2. Als kookpiek: verschuif start naar 18:30 of eerder als surplus dan al weg
# 3. Stagger: tweede toestel minimum 15 min na eerste
# 4. Bij force-start: check can_start_appliance(is_force_start=True, hard_deadline=...)
#    - Als hard_deadline >= 18:45 en kookpiek: wacht tot 18:30 (betere keuze dan piek creëren)
#    - Als hard_deadline < 18:45 of kookpiek voorbij: start toch
# 5. Log: "Start uitgesteld: kookpiek / 15-min stagger / force-wait-18:30"
```

---

### 14.6 DHW Temperatuur & SSW Modus — Bevestigd

```python
# Bevestigde entiteiten (2026-05-05):
DHW_ENTITIES = {
    # Lezen
    "dhw_temp_c":        "sensor.opentherm_sww_temperatuur",          # 46.1°C ✅
    "water_temp_c":      "sensor.opentherm_watertemperatuur",          # 49.3°C
    "return_temp_c":     "sensor.opentherm_retourtemperatuur",         # 49.2°C
    "dhw_status":        "binary_sensor.opentherm_sww_status",         # actief opwarmen
    "flame_active":      "binary_sensor.opentherm_vlamstatus",         # brander aan
    "ssw_modus":         "select.opentherm_ssw_modus",                 # off|boost|auto|comfort|eco
    "dhw_setpoint":      "number.opentherm_instelpunt_sanitair_warm_water",  # 35-60°C, nu 50°C
    "max_boiler_temp":   "number.opentherm_instelpunt_maximale_boilertemperatuur",  # 25-90°C
    
    # Schrijven (via select — beter dan aan/uit switch)
    "set_ssw_modus":     ("select", "select_option", "select.opentherm_ssw_modus"),
}

# ⚠️ SSW Modus selectie — gebruik dit NIET de switch:
# switch.opentherm_sww_comfortmodus is te beperkt (alleen aan/uit)
# select.opentherm_ssw_modus geeft volledige controle:
#
# HeatPumpAgent.set_dhw_boost(True)  → select_option: "boost"
# HeatPumpAgent.set_dhw_boost(False) → select_option: "comfort" (terug naar normaal)
# Vakantie modus                     → select_option: "eco" of "off"
# 's Nachts (AUTO 2)                 → select_option: "eco"

class DHWMonitor:
    """
    Tracks DHW temperature using confirmed sensor.opentherm_sww_temperatuur.
    
    With confirmed sensor available:
    - Know exact moment water is warm enough (>= TARGET_TEMP_C)
    - Stop boost early if target reached (energy saving)
    - Predict when water will run cold via learned cooling rate
    - Target: water >= 55°C at 20:00 for evening shower peak
    
    Why 55°C target (not lower):
        A hotter stored temperature means more shower capacity from the same volume.
        Primary reason: anti-legionella buffer — Legionella bacteria thrive at 25-50°C.
        Daily target of 55°C provides safety margin above the danger zone.
        NOTE: The Thermastage heat pump has its own built-in anti-legionella program
        that periodically heats water to 60°C+. EnergyBrain must NOT interfere with
        this cycle — treat any temporary boost above 58°C as normal pump behavior,
        not an error condition.
    
    DHW boost decision logic:
        Solar surplus available AND DHW < TARGET_TEMP_C (55°C) → boost opportunistically
        DHW < HARD_MIN_TEMP_C (45°C) before 18:00                → force boost (hard limit)
        DHW < WATCHDOG_TEMP_C (40°C) after 17:00                 → watchdog emergency boost
        DHW >= BOOST_STOP_TEMP_C (58°C)                          → stop boost early
        DHW >= 58°C due to legionella cycle                      → normal, no action
    
    DHW cooling model (physics-informed LinearRegression):
        Newton's law of cooling: dT/dt = -k × (T_water - T_outdoor)
        LinearRegression learns constant k from history.
        Feature: (dhw_temp_c - outdoor_temp_c) → delta_dhw_per_hour
        This is intentionally kept linear — physics dictates the structure.
        Housed in ThermalModel.predict_dhw_temperature() — not a separate model.
        
        Why not GBC here: the heat loss equation is a known physical law.
        Using ML would learn a noisier approximation of something we already know.
        LinearRegression learns only the coefficient k, which is the correct approach.
    """
    
    TARGET_TEMP_C = 55.0
    BOOST_STOP_TEMP_C = 58.0         # Stop EnergyBrain boost at this temp
    HARD_MIN_TEMP_C = 45.0           # Force boost below this before 18:00 (hard limit)
    WATCHDOG_TEMP_C = 40.0           # Watchdog emergency threshold after 17:00
    LEGIONELLA_TEMP_C = 60.0         # Above this = heat pump legionella cycle, ignore
```

---

### 14.7 Dynamisch Prijscontract Architectuur

```python
class EnergyPriceAgent(BaseAgent):
    """
    Supports both static and dynamic energy contracts.
    
    Current situation (static - Eneco):
        Import: ~€0.25/kWh (fixed)
        Export: ~€0.036/kWh (BELPEX formula, monthly average)
        Capacity: €47.50/kW/year excl. BTW (2026, Fluvius — verify annually)
    
    Future situation (dynamic - ready to activate):
        Import: ENTSO-E day-ahead prices + grid fees + taxes
        Export: ENTSO-E day-ahead prices
        Activation: set CONTRACT_TYPE=dynamic in .env
    
    Architecture:
        PriceProvider (abstract)
        ├── StaticPriceProvider    ← current, CONTRACT_TYPE=static
        └── DynamicPriceProvider   ← future, CONTRACT_TYPE=dynamic
                                      uses ENTSO-E API
    
    Dynamic pricing impact on DayPlanner:
        - Prefer starting appliances in cheap hours (< 70% daily avg)
        - Prefer battery charge in cheap hours
        - Battery discharge in expensive hours (if Marstek write enabled)
        - Never start large loads in expensive hours unless deadline forced
    """
    
    # Both providers return same EnergyPrice dataclass
    # DayPlanner logic works identically — only prices change
```

**.env additions:**
```bash
# Contract type: static (current) or dynamic (ENTSO-E)
CONTRACT_TYPE=static

# Dynamic contract thresholds (active when CONTRACT_TYPE=dynamic)
CHEAP_HOUR_THRESHOLD_PCT=70     # % of daily avg below which = cheap
EXPENSIVE_HOUR_THRESHOLD_PCT=130 # % above which = expensive
```

---

### 14.8 Start Verificatie & Retry

```python
class ApplianceStartVerifier:
    """
    Verifies that a start command actually resulted in the appliance running.
    
    Process:
    1. Check door is closed (sensor.*_deur = closed) before sending start command
    2. Send start command via HomeConnectAgent
    3. Wait VERIFICATION_DELAY_S seconds
    4. Check sensor.*_status == 'run' OR 'delayedstart' (not just is_running)
    4a. If running/delayedstart: log success, send notification
    4b. If not: retry once after RETRY_DELAY_S
    5a. Retry succeeds: log warning
    5b. Retry fails: log error, send WARNING push to user
    
    Also verifies DHW boost:
    - Check switch.opentherm_sww_comfortmodus state 30s after command
    - If still off: retry once, then alarm
    """
    
    VERIFICATION_DELAY_S = 60
    RETRY_DELAY_S = 120
    MAX_RETRIES = 1
    
    async def start_and_verify(
        self,
        appliance: ApplianceType,
        agent: HomeConnectAgent,
    ) -> tuple[bool, str]:
        """
        Returns (success, message).
        Handles retry internally.
        """
```
-e 
---

## 15. Handmatige Override & Event Modus

### 15.1 HVAC Override detectie

```python
class HeatPumpAgent:
    """
    Detecteert wanneer de gebruiker de Anna manueel heeft aangepast.
    EnergyBrain respecteert dit voor de ingestelde duur.

    Detectielogica (elke cyclus):
        last_set = state_store.get("last_set_setpoint_c")
        current  = climate.anna.attributes.temperature
        Als |current - last_set| > 0.5°C EN brain heeft niet zelf gewijzigd:
            → Handmatige override gedetecteerd

    Tijdens override:
        - Setpoint NIET aanpassen
        - Loggen wat het brein "zou" hebben gedaan
        - Safety watchdog blijft actief (indoor < 17°C → force heat)
        - input_boolean.energybrain_hvac_override_active = True (via HA)
        - input_datetime.energybrain_hvac_override_expires = nu + duur

    Override verloopt via HA timer automatie:
        Wanneer timer afloopt → input_boolean = False
        EnergyBrain detecteert False → stuurt "hervat controle" notificatie
    """

    OVERRIDE_DETECTION_DELTA_C = 0.5
    DEFAULT_OVERRIDE_DURATION_H = 8

    def detect_manual_override(
        self,
        current_setpoint: float,
        last_set_setpoint: float,
        brain_changed_this_cycle: bool,
    ) -> bool:
        """True als gebruiker Anna handmatig heeft aangepast."""
        if brain_changed_this_cycle:
            return False
        return abs(current_setpoint - last_set_setpoint) > self.OVERRIDE_DETECTION_DELTA_C
```

### 15.2 Override flow in DayPlanner

```python
class DayPlanner:

    async def run_cycle(self, control_state: ControlState) -> None:
        """
        Prioriteitsvolgorde elke 60 seconden:

        1. Systeem uitgeschakeld?
           input_boolean.energybrain_enabled = off → doe niets, log "uitgeschakeld"

        2. Vakantie modus?
           input_boolean.energybrain_vacation_mode = on
           → Zet Anna op vacation preset
           → Stop alle toestelplanning
           → Batterij op passief

        3. Handmatige Anna setpoint gedetecteerd?
           HeatPumpAgent.detect_manual_override() = True
           → ONMIDDELLIJK nieuwe voorkeur opslaan in ComfortLearner
           → Geen timer, geen terugzetten
           → Stuur notificatie: "Nieuw setpoint {temp}°C geregistreerd als voorkeur"

        4. Normale brein logica:
           → ComfortLearner.predict_preferred_setpoint() → target setpoint
           → WeekStrategist strategie ophalen
           → PV forecast + PatternLearner combineren
           → Toestellen plannen
           → HVAC setpoint instellen op basis van ComfortLearner
           → Status schrijven naar input_text helpers

        5. Outlier dag detectie (elke dag om 23:00):
           PatternLearner.is_outlier_day() → mark in DB
           → Dag NIET gebruikt voor modeltraining
           → Wel geoptimaliseerd in real-time (normale brein logica)

        SAFETY (altijd, ongeacht alles):
           Watchdog checkt elke 5 min onafhankelijk van DayPlanner.
        """
```

**ComfortLearner** (onderdeel van PatternLearner):
```python
class ComfortLearner:
    """
    Leert jouw thermische voorkeuren uit gedrag.
    Geen hardcoded doeltemperatuur — brein leert wat jij wil.
    
    Wanneer jij Anna aanpast:
        → Onmiddellijk nieuwe voorkeur, geen timer
        → Record: (outdoor_temp, season, hour, weekday) → setpoint
    
    Wanneer brein denkt dat het setpoint verlaagd kan worden:
        → Stuur VRAAG-notificatie, pas NIET automatisch aan
        → "🌡️ Het is 11°C buiten, huis is 21.2°C. Mag ik verlagen naar 20°C?"
        → Ja: brein past aan + leert | Nee: brein leert dat 21°C correct was
    
    Na ~20 observaties:
        → Brein stelt setpoint proactief in op basis van buitentemperatuur
        → Jij hoeft Anna nooit meer aan te raken
    
    Voorbeeld jaar 2:
        Koude winteravond → brein zet automatisch 21°C
        Milde voorjaarsmiddag → brein zet 19.5°C
        Jij corrigeert nooit meer → voorkeur goed geleerd
    
    Algoritme: GradientBoostingRegressor
    Features: outdoor_temp_c, season_q, hour, weekday, is_school_holiday
    Target: user_preferred_setpoint_c
    MIN_OBSERVATIONS = 20
    """
```

### 15.3 Notificaties voor override events

```python
# Notificatietypes voor ComfortLearner en override
NotificationType.COMFORT_PREFERENCE_RECORDED:
    title: "🌡️ Temperatuurvoorkeur geregistreerd"
    body:  "Setpoint {temp}°C opgeslagen als nieuwe voorkeur bij {outdoor}°C buiten."

NotificationType.COMFORT_SETPOINT_SUGGESTION:
    title: "🌡️ Mag ik de temperatuur aanpassen?"
    body:  "Het is {outdoor}°C buiten en {indoor}°C binnen. Mag ik verlagen naar {suggested}°C?"
    action_yes: "Ja, pas aan"     # Brein past aan + leert
    action_no:  "Nee, laat zo"   # Brein leert dat huidige temp correct was

NotificationType.VACATION_MODE_ACTIVATED:
    title: "🏖️ Vakantie modus actief"
    body:  "EnergyBrain in spaarmodus tot {end_date}."

NotificationType.OUTLIER_DAY_DETECTED:
    title: "📊 Afwijkende dag gedetecteerd"
    body:  "Vandaag niet meegenomen in leermodellen (ongewoon patroon)."
    throttle: "1_per_day"
```

---

## 16. HA Besturingsinterface

Alle bestanden aangemaakt als HA packages — Claude Code plaatst deze in `ha_config/`.

### 16.1 Input Helpers package

**Bestand:** `ha_config/packages/energybrain_control.yaml`

Bevat alle `input_boolean`, `input_number`, `input_select`, `input_datetime`,
`input_text` en `timer` helpers die EnergyBrain leest en schrijft.
Claude Code genereert dit bestand volledig in Fase 9 — Deployment.

Volledige lijst:
```yaml
input_boolean:
  energybrain_enabled:              # Systeem aan/uit
  energybrain_vacation_mode:        # Vakantie modus
  energybrain_dhw_boost_now:        # Direct DHW boosten

input_number:
  energybrain_dhw_target_temp:      # 50 – 60 °C, step 1, default 55

input_select:
  energybrain_mode:                 # normaal | eco | boost

input_datetime:
  energybrain_vacation_start:       # has_date: true, has_time: false
  energybrain_vacation_end:         # has_date: true, has_time: false

input_text:
  energybrain_status:               # "Actief | 2.3 kW surplus"
  energybrain_last_action:          # "Vaatwasser gestart om 11:23 op zonnestroom"
  energybrain_today_plan:           # "Zon verwacht 9-15u. Wasmachine: 10:30."
  energybrain_next_action:          # "DHW boost om 14:00 (verwacht laag om 20u)"

timer:
  energybrain_dhw_boost_reset:      # Reset DHW boost knop na 5 min
```

**Temperatuurvoorkeur wordt NIET via helpers ingesteld.**
Jij past Anna aan → brein registreert onmiddellijk als nieuwe voorkeur.
Geen schuifje, geen knop. Het brein leert van wat je doet, niet van wat je instelt.

### 16.2 Automaties package

**Bestand:** `ha_config/packages/energybrain_automations.yaml`

```yaml
# Timer automaties — beheren override verloop zonder Python code
automation:

  - alias: "EnergyBrain | HVAC Override starten"
    trigger:
      - platform: state
        entity_id: input_boolean.energybrain_hvac_override_active
        to: "on"
    action:
      - service: timer.start
        target:
          entity_id: timer.energybrain_hvac_override_timer
        data:
          duration: >
            {{ states('input_number.energybrain_hvac_override_duration_h') | int * 3600 }}
      - service: input_datetime.set_datetime
        target:
          entity_id: input_datetime.energybrain_hvac_override_expires
        data:
          datetime: >
            {{ (now() + timedelta(hours=states('input_number.energybrain_hvac_override_duration_h') | int)).strftime('%Y-%m-%d %H:%M:%S') }}

  - alias: "EnergyBrain | HVAC Override verlopen"
    trigger:
      - platform: event
        event_type: timer.finished
        event_data:
          entity_id: timer.energybrain_hvac_override_timer
    action:
      - service: input_boolean.turn_off
        target:
          entity_id: input_boolean.energybrain_hvac_override_active

  - alias: "EnergyBrain | Feest modus starten"
    trigger:
      - platform: state
        entity_id: input_boolean.energybrain_party_mode
        to: "on"
    action:
      - service: timer.start
        target:
          entity_id: timer.energybrain_party_timer
        data:
          duration: >
            {{ states('input_number.energybrain_party_duration_h') | int * 3600 }}
      - service: input_datetime.set_datetime
        target:
          entity_id: input_datetime.energybrain_party_expires
        data:
          datetime: >
            {{ (now() + timedelta(hours=states('input_number.energybrain_party_duration_h') | int)).strftime('%Y-%m-%d %H:%M:%S') }}

  - alias: "EnergyBrain | Feest modus verlopen"
    trigger:
      - platform: event
        event_type: timer.finished
        event_data:
          entity_id: timer.energybrain_party_timer
    action:
      - service: input_boolean.turn_off
        target:
          entity_id: input_boolean.energybrain_party_mode

  - alias: "EnergyBrain | DHW boost reset"
    description: "Reset DHW boost knop na 5 minuten — EnergyBrain heeft het opgepikt"
    trigger:
      - platform: state
        entity_id: input_boolean.energybrain_dhw_boost_now
        to: "on"
        for: "00:05:00"
    action:
      - service: input_boolean.turn_off
        target:
          entity_id: input_boolean.energybrain_dhw_boost_now
```

### 16.3 HA Dashboard

**Bestand:** `ha_config/dashboards/energybrain.yaml`

```yaml
title: EnergyBrain
icon: mdi:brain
path: energybrain
cards:

  # ─── STATUS BANNER ───────────────────────────────────────
  - type: markdown
    content: >
      ## 🧠 EnergyBrain
      **Status:** {{ states('input_text.energybrain_status') }}
      **Laatste actie:** {{ states('input_text.energybrain_last_action') }}
      **Vandaag:** {{ states('input_text.energybrain_today_plan') }}
      **Volgende:** {{ states('input_text.energybrain_next_action') }}

  # ─── SYSTEEM AAN/UIT ─────────────────────────────────────
  - type: entities
    title: Systeem
    entities:
      - entity: input_boolean.energybrain_enabled
        name: EnergyBrain actief
      - entity: input_select.energybrain_mode
        name: Modus

  # ─── VAKANTIE MODUS ──────────────────────────────────────
  - type: entities
    title: 🏖️ Vakantie
    entities:
      - entity: input_boolean.energybrain_vacation_mode
        name: Vakantie modus
      - entity: input_datetime.energybrain_vacation_start
        name: Vertrekdatum
      - entity: input_datetime.energybrain_vacation_end
        name: Terugkeerdatum

  # ─── FEEST MODUS ─────────────────────────────────────────
  - type: vertical-stack
    cards:
      - type: entities
        title: 🎉 Feest / Gasten
        entities:
          - entity: input_boolean.energybrain_party_mode
            name: Feest modus
          - entity: input_number.energybrain_party_temp_offset
            name: Temperatuur extra (°C)
          - entity: input_number.energybrain_party_duration_h
            name: Duur (uur)
          - entity: input_boolean.energybrain_party_extra_cooking
            name: Meer koken verwacht
          - entity: input_number.energybrain_party_dishwasher_runs
            name: Vaatwasser beurten
          - entity: input_datetime.energybrain_party_expires
            name: Actief tot
            
      - type: conditional
        conditions:
          - entity: input_boolean.energybrain_party_mode
            state: "on"
        card:
          type: markdown
          content: >
            ✅ **Feest modus actief tot
            {{ states('input_datetime.energybrain_party_expires') | as_timestamp | timestamp_custom('%H:%M') }}**
            Temperatuur +{{ states('input_number.energybrain_party_temp_offset') }}°C |
            Vaatwasser {{ states('input_number.energybrain_party_dishwasher_runs') | int }}×

  # ─── TEMPERATUUR OVERRIDE ────────────────────────────────
  - type: vertical-stack
    cards:
      - type: entities
        title: 🌡️ Temperatuur override
        entities:
          - entity: input_boolean.energybrain_hvac_override_active
            name: Override actief
          - entity: input_number.energybrain_hvac_override_setpoint
            name: Gewenste temperatuur (°C)
          - entity: input_number.energybrain_hvac_override_duration_h
            name: Duur (uur)
          - entity: input_datetime.energybrain_hvac_override_expires
            name: Actief tot

      - type: conditional
        conditions:
          - entity: input_boolean.energybrain_hvac_override_active
            state: "on"
        card:
          type: markdown
          content: >
            ✅ **Override actief:
            {{ states('input_number.energybrain_hvac_override_setpoint') }}°C
            tot {{ states('input_datetime.energybrain_hvac_override_expires') | as_timestamp | timestamp_custom('%H:%M') }}**
            EnergyBrain past de temperatuur niet aan tot de override verloopt.

  # ─── WARM WATER ──────────────────────────────────────────
  - type: entities
    title: 🚿 Warm water
    entities:
      - entity: sensor.opentherm_sww_temperatuur
        name: Huidige boilertemperatuur
      - entity: input_number.energybrain_dhw_target_temp
        name: Doeltemperatuur (°C)
      - entity: input_boolean.energybrain_dhw_boost_now
        name: Nu boosten

  # ─── LIVE ENERGIE ────────────────────────────────────────
  - type: glance
    title: ⚡ Live energie
    entities:
      - entity: sensor.goodwe_pv_power
        name: Zonnepanelen
      - entity: sensor.p1_meter_power_w
        name: Net
      - entity: sensor.marstek_venuse_soc
        name: Batterij SoC
      - entity: sensor.opentherm_sww_temperatuur
        name: Boiler

  # ─── TOESTELLEN ──────────────────────────────────────────
  - type: entities
    title: 🏠 Toestellen
    entities:
      - entity: sensor.vaatwasser_status
        name: Vaatwasser
      - entity: binary_sensor.vaatwasser_start_op_afstand
        name: Klaar voor start
      - entity: sensor.wasmachine_status
        name: Wasmachine
      - entity: binary_sensor.wasmachine_start_op_afstand
        name: Klaar voor start
      - entity: sensor.droger_status
        name: Droger
      - entity: binary_sensor.droger_start_op_afstand
        name: Klaar voor start

  # ─── SYSTEEM INFO ─────────────────────────────────────────
  - type: entities
    title: 🔧 Systeem info
    entities:
      - entity: sensor.goodwe_work_mode
        name: GoodWe modus
      - entity: select.marstek_venuse_operating_mode
        name: Marstek modus
      - entity: input_text.energybrain_override_reason
        name: Override reden
```

---

*EnergyBrain v3.2 — David Van Ham — Korbeek-lo, Bierbeek*
*Open source | Spec: 2026-05-04 | Laatste update: 2026-05-08 (v3.2: HAControlAgent + UI + Override logica)*

**Grootste open punt: Marstek V154 firmware**
BatteryDispatcher (MPC) staat klaar en berekent dagelijks de optimale strategie.
De Waveshare USB-RS485 adapter is al geïnstalleerd en lezen werkt.
V153 heeft een bekende write-regressie bug — schrijven was kapot na V153 update.
Zodra V154 beschikbaar is als OTA update: `MARSTEK_WRITE_ENABLED=true` in `.env`.
Het systeem heeft dan reeds maanden ML-data om direct optimaal te sturen.

**Te testen open punt: Anna koeling**
Zet bij eerste warme dag (>22°C) hvac_mode=auto + setpoint=18°C en observeer hvac_action.
Als 'cooling' verschijnt: zet COOLING_ENABLED=True en activeer WeekStrategist koelingslogica.
Als geen reactie: koeling vereist €300 Thermastage feature via Thercon Belgium.
