# CLAUDE.md — EnergyBrain
<!-- Updated: 2026-05-08 | Spec versie: v3.1 | Status: Bouwklaar -->

## Wat is EnergyBrain?

Een vooruitdenkend, lerend energiebeheersysteem voor een eengezinswoning in
Korbeek-lo, Bierbeek, België. Het coördineert zonnepanelen, batterij, warmtepomp
en slimme toestellen zodat zo veel mogelijk energie van eigen PV gebruikt wordt.

**Volledige specificatie:** `ENERGYBRAIN_CLAUDE_CODE_SPEC.md`
Lees de relevante sectie van de spec VOOR je aan een fase begint.
De spec is de enige bron van waarheid voor entity IDs, datamodellen en logica.

---

## Huidige status — Bouwklaar

Onderzoek volledig afgerond. Alle entity IDs geverifieerd via HA Developer Tools.

| Apparaat | Model | Integratie | Status |
|---|---|---|---|
| GoodWe omvormer | GW5K-ET 3-fase | HA native UDP 8899 | ✅ Verbonden |
| Marstek batterij | Venus E 5.12kWh | jaapp/ha-marstek-local-api v1.1.0 | ✅ Lezen OK |
| Marstek batterij | Venus E 5.12kWh | RS485 Waveshare adapter | ⚠️ Schrijven STUB (V153 bug) |
| HomeWizard P1 | Slimme meter | Lokale REST API | ✅ Verbonden |
| Plugwise Anna | OpenTherm thermostaat | HA Plugwise integratie | ✅ Verbonden |
| Siemens vaatwasser | SN65ZX49CE/14 | Home Connect / HA | ✅ Verbonden |
| Siemens wasmachine | WG44B2A5NL/11 | Home Connect / HA | ✅ Verbonden |
| Siemens droger | WQ45B2A5NL/02 | Home Connect / HA | ✅ Verbonden |

---

## Kritieke constraints — lees dit vóór je iets bouwt

### Marstek batterij
- AC-gekoppeld op L1 — GoodWe ziet de batterij NIET
- `sensor.goodwe_battery_power` = altijd 0 → NIET GEBRUIKEN
- Primaire batterijdata: MarstekAgent via `sensor.marstek_venuse_*`
- **V153 firmware write-bug:** RS485 adapter aanwezig, schrijven geblokkeerd door firmware regressie
- Fix verwacht in V154. Tot dan: alle schrijfmethoden zijn STUBS die loggen maar niets uitvoeren
- Marstek staat momenteel op **Manual modus** — batterij doet niets. Zet terug op **Auto** tot V154

### GoodWe omvormer
- Gaat 's nachts in slaapstand → alle realtime sensors `unavailable` → gebruik 0W als fallback
- `peak_shaving` modus NIET bruikbaar (SolarGo niet geconfigureerd)
- Gebruik `GENERAL` modus als standaard. `export_limit_w` als stuurmiddel.

### Anna warmtepomp
- Koeling: ONBEKEND of dit werkt zonder €300 Thermastage feature
- `COOLING_ENABLED=False` tot getest. Test procedure: zie spec sectie 14.2
- Test pas mogelijk bij eerste warme dag (>22°C buiten)
- Schema (`select.anna_thermostaat_schema`) moet altijd op `off` staan
- Check bij startup EN elke 15 min — nooit samen met EnergyBrain laten draaien

### Home Connect toestellen
- Wasmachine/droger delay eenheid: `max=100` bij unavailable — betekenis onbekend
- Vaatwasser delay: bevestigd `unit=s`, `max=86400` (24 uur)
- Start enkel als `remote_sensor = on` EN `door_sensor = closed`

---

## Projectstructuur

```
energybrain/
├── CLAUDE.md
├── ENERGYBRAIN_CLAUDE_CODE_SPEC.md
├── README.md
├── pyproject.toml
├── .env                             ← NOOIT aanraken, lezen of committen
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
│   │   └── orchestrator.py
│   │
│   ├── intelligence/
│   │   ├── thermal_model.py         ← LinearRegression 5 features → GBR upgrade pad
│   │   ├── pv_forecaster.py         ← Ridge Regression + POA berekening
│   │   ├── pattern_learner.py       ← 5× GradientBoosting, 9 features, 3 lagen
│   │   ├── outcome_tracker.py       ← feedback loop + drift + maandrapport
│   │   ├── battery_dispatcher.py    ← MPC scipy linprog (STUB tot V154)
│   │   ├── day_planner.py
│   │   ├── week_strategist.py
│   │   └── oscillation_detector.py
│   │
│   ├── agents/
│   │   ├── base_agent.py
│   │   ├── goodwe_agent.py
│   │   ├── marstek_agent.py         ← schrijfmethoden zijn STUBS (V153 bug)
│   │   ├── p1_agent.py              ← primaire grid data bron
│   │   ├── heat_pump_agent.py       ← incl. manual override detectie
│   │   ├── home_connect_agent.py
│   │   ├── ha_agent.py
│   │   ├── ha_control_agent.py      ← leest/schrijft input helpers (besturingsinterface)
│   │   ├── weather_agent.py
│   │   ├── energy_price_agent.py
│   │   └── notification_agent.py
│   │
│   ├── safety/
│   │   ├── hard_limits.py
│   │   ├── watchdog.py
│   │   └── rollback.py
│   │
│   ├── persistence/
│   │   ├── database.py
│   │   ├── state_store.py
│   │   └── learning_store.py
│   │
│   └── utils/
│       ├── ha_client.py
│       ├── retry.py
│       └── logging_config.py
│
├── tests/
│   ├── conftest.py
│   ├── test_intelligence/
│   ├── test_agents/
│   ├── test_safety/
│   └── integration/                 ← @pytest.mark.integration — draait op Pi
│
└── scripts/
    ├── check_ha_connection.py
    ├── check_all_agents.py
    ├── simulate_day.py
    └── train_models.py

ha_config/                           ← HA configuratiebestanden (apart van Python code)
├── packages/
│   ├── energybrain_control.yaml     ← alle input helpers (booleans, numbers, selects)
│   └── energybrain_automations.yaml ← timer automaties voor override verloop
└── dashboards/
    └── energybrain.yaml             ← Lovelace dashboard
```

---

## Teststrategie — twee lagen, beide verplicht

**Laag 1: Unit tests** — draaien overal, ook offline. Gebruik gesimuleerde HA-responses.
**Laag 2: Integratie tests** — draaien op Pi met hardware. `pytest -m integration`

---

## Bouwfasen — werk fase per fase

Na elke fase: run tests, rapporteer resultaat, ga pas verder als alles slaagt.

### Fase 0 — Fundament (spec secties 2, 3, 8)
`pyproject.toml` | `.env.template` | `models.py` | `exceptions.py` | `config.py`
`logging_config.py` | `retry.py` | `database.py` (inclusief `predictions` + `accuracy_reports` tabellen)

### Fase 1 — HA communicatielaag
`ha_client.py` | `base_agent.py`

### Fase 2 — Safety systeem (VOOR intelligence)
`hard_limits.py` | `watchdog.py` | `rollback.py`

### Fase 3 — Data agents
`p1_agent` → `goodwe_agent` → `marstek_agent (stub)` → `heat_pump_agent (+ override detectie)`
→ `weather_agent` → `home_connect_agent` → `ha_control_agent` → `notification_agent` → `energy_price_agent`

### Fase 4 — Intelligence (in volgorde — afhankelijkheden)
1. `oscillation_detector`
2. `thermal_model` (5 features: outdoor + solar + wind + hour + hvac_active)
3. `pv_forecaster` (Ridge Regression, 5 features)
4. `pattern_learner` (5× GBC/GBR, 9 features)
5. `outcome_tracker` (gebruikt door alle bovenstaande)
6. `battery_dispatcher` (MPC STUB)
7. `day_planner`
8. `week_strategist`

### Fase 5 — Orchestrator & startup
`orchestrator.py` | `main.py` | `state_store.py` | `learning_store.py`
Startup: GoodWe → GENERAL, Marstek modus check, Anna schema check

### Fase 6 — Integratie & scripts
`simulate_day.py` | volledige integratie tests | coverage ≥ 80%

### Fase 7 — HA besturingsinterface (spec sectie 16)
`ha_config/packages/energybrain_control.yaml`   ← alle input helpers
`ha_config/packages/energybrain_automations.yaml` ← timer automaties
`ha_config/dashboards/energybrain.yaml`           ← Lovelace dashboard

Installatie in HA:
1. Kopieer `ha_config/` naar HA config map
2. Voeg toe aan `configuration.yaml`:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages/
   ```
3. Voeg dashboard toe via HA UI → Dashboards → Add dashboard → Upload YAML

### Fase 8 — Add-on verpakking (spec sectie 17)
`energybrain-addon/repository.yaml`
`energybrain-addon/energybrain/config.yaml`  ← add-on manifest
`energybrain-addon/energybrain/Dockerfile`
`energybrain-addon/energybrain/run.sh`

Installatie in HA:
1. Push `energybrain-addon` repo naar GitHub
2. HA → Settings → Add-ons → Add-on store → ⋮ → Repositories → URL toevoegen
3. Installeer EnergyBrain add-on
4. Configureer token en instellingen
5. Start add-on

---

## ML model overzicht

| Module | Algoritme | Activatie |
|---|---|---|
| ThermalModel | LinearRegression → GBR als R² < 0.85 na 90d | Dag 15+ |
| PVForecaster | Ridge Regression kalibratie | Dag 30+ |
| PatternLearner (DHW + 3 toestellen + kookpiek) | 5× GradientBoosting | Dag 15+ |
| DHW afkoeling | LinearRegression (Newton) | Dag 15+ |
| BatteryDispatcher | MPC via scipy.optimize.linprog | Na V154 firmware |
| OutcomeTracker | Predicted vs actual vergelijking | Dag 30+ |

Na 365 dagen: OutcomeTracker evalueert XGBoost/LightGBM upgrade — handmatige bevestiging vereist.

---

## Coding conventies

- **Code & comments:** Engels | **Docstrings:** Google-stijl Engels
- **Python:** 3.11+ | PEP8 | type hints overal | `snake_case`
- **Config:** altijd via `config.py` → `.env` — nooit hardcoded
- **Logging:** `logging_config.py` — geen `print()` in productie
- **Async:** `asyncio` voor alle I/O
- **ML modellen:** opslaan via `joblib` naar SQLite `model_blob` kolom
- **Retry:** alle HA calls via `retry.py` met exponential backoff

---

## Git & GitHub

- Branches: `feature/<fase-naam>`
- `git add` + `git commit`: Claude Code mag vrij doen
- `git push` + `git merge`: altijd bevestiging vragen
- Conventional Commits: `feat:` | `fix:` | `test:` | `chore:` | `docs:`
- Nooit committen: `.env` | `energybrain.db` | logbestanden | hardcoded IPs

---

## Absolute regels

### NOOIT
- `.env` aanraken, lezen of ernaar verwijzen in code
- IP-adressen, tokens of wachtwoorden hardcoden
- Bestanden verwijderen
- `git push` of `git merge` zonder bevestiging
- Marstek schrijfstubs vervangen door echte calls (wacht op V154)
- GoodWe `peak_shaving` activeren

### ALTIJD bevestiging vragen voor
- Dependencies toevoegen aan `pyproject.toml`
- Architectuurwijzigingen over meerdere modules
- Aanpassingen aan bestaande werkende code

---

## Omgevingsvariabelen (namen — nooit waarden)

```
HA_URL=                          MARSTEK_WRITE_ENABLED=false
HA_TOKEN=                        CONTRACT_TYPE=static
NOTIFICATION_DEVICE=             CHEAP_HOUR_THRESHOLD_PCT=
GOODWE_IP=                       EXPENSIVE_HOUR_THRESHOLD_PCT=
MARSTEK_IP=                      CYCLE_INTERVAL_S=60
HOMEWIZARD_IP=                   WATCHDOG_INTERVAL_S=300
ANNA_IP=                         LOG_LEVEL=INFO
ENTSOE_API_KEY=                  DB_PATH=
STATIC_IMPORT_PRICE_EUR_KWH=     DB_RETENTION_DAYS=90
STATIC_EXPORT_PRICE_EUR_KWH=     DB_HOURLY_RETENTION_YEARS=2
CAPACITY_TARIFF_EUR_KW_YEAR=     DHW_TARGET_TEMP_C=55.0
LATITUDE=50.8597                 START_VERIFY_DELAY_S=60
LONGITUDE=4.7628                 START_RETRY_DELAY_S=120
TIMEZONE=Europe/Brussels         THERMAL_R2_UPGRADE_THRESHOLD=0.85
SURPLUS_DHW_W=                   DRIFT_THRESHOLD_PCT=15.0
SURPLUS_DISHWASHER_W=            DRIFT_WINDOW_DAYS=14
SURPLUS_WASHING_MACHINE_W=       ACCURACY_BASELINE_DAYS=30
SURPLUS_DRYER_W=                 BATTERY_MPC_HORIZON_HOURS=24
SURPLUS_BATTERY_W=               BATTERY_MPC_TIMESTEP_MIN=15
SURPLUS_HVAC_W=                  PATTERN_LEARNER_MIN_DAYS_BASIC=14
BATTERY_SOC_MIN_PCT=             PATTERN_LEARNER_MIN_DAYS_SEASONAL=90
BATTERY_SOC_DHW_MIN_PCT=         PATTERN_LEARNER_MIN_DAYS_YEARLY=365
BATTERY_SOC_APPLIANCE_MIN_PCT=   PV_CALIBRATION_MIN_DAYS=30
HVAC_MAX_SETPOINT_C=             THERMAL_MODEL_MIN_SAMPLES=336
HVAC_MIN_SETPOINT_C=             COOKING_PEAK_START_DEFAULT=17:00
HVAC_MAX_STEP_PER_CYCLE_C=       COOKING_PEAK_END_DEFAULT=18:30
HVAC_FROST_OUTDOOR_C=            MIN_GAP_BETWEEN_STARTS_MIN=15
INDOOR_TEMP_MIN_WINTER_C=        DISHWASHER_MAX_WAIT_H=4
                                 WASHING_MACHINE_MAX_WAIT_H=6
                                 DRYER_MAX_WAIT_H=8
```

---

## Open punten

| Punt | Status | Actie |
|---|---|---|
| Marstek schrijven | V153 bug — wacht op V154 OTA | Stub laten staan |
| Koeling via Anna | Test bij >22°C buiten | COOLING_ENABLED=False |
| Wasmachine/droger delay eenheid | max=100 bij unavailable | Testen wanneer actief |

---

## Autonomiedoelstelling

Na 90 dagen ~75% | Na 365 dagen ~92% | Resterende 8% onbereikbaar zonder externe context.
Maandelijkse zelfrapportage: accuraatheid, drift, besparingen, feature importances.
