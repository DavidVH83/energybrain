"""Tests for energybrain.config."""
from datetime import time
from pathlib import Path

import pytest

from energybrain.config import load_config
from energybrain.exceptions import ConfigError


class TestLoadConfig:
    def test_loads_with_minimal_env(self, minimal_env):
        cfg = load_config()
        assert cfg.ha_url == "http://localhost:8123"
        assert cfg.ha_token == "test-token"
        assert cfg.notification_device == "test_device"

    def test_defaults_are_applied(self, minimal_env):
        cfg = load_config()
        assert cfg.latitude == pytest.approx(50.8597)
        assert cfg.longitude == pytest.approx(4.7628)
        assert cfg.timezone == "Europe/Brussels"
        assert cfg.marstek_write_enabled is False
        assert cfg.cycle_interval_s == 60

    def test_surplus_defaults(self, minimal_env):
        cfg = load_config()
        assert cfg.surplus_dhw_w == 2000.0
        assert cfg.surplus_dishwasher_w == 1800.0
        assert cfg.surplus_battery_w == 500.0

    def test_hvac_safety_defaults(self, minimal_env):
        cfg = load_config()
        assert cfg.hvac_max_setpoint_c == pytest.approx(22.5)
        assert cfg.hvac_min_setpoint_c == pytest.approx(16.0)
        assert cfg.hvac_frost_outdoor_c == pytest.approx(-2.0)

    def test_battery_soc_defaults(self, minimal_env):
        cfg = load_config()
        assert cfg.battery_soc_min_pct == 10.0
        assert cfg.battery_soc_dhw_min_pct == 50.0
        assert cfg.battery_soc_appliance_min_pct == 70.0

    def test_cooking_peak_times(self, minimal_env):
        cfg = load_config()
        assert cfg.cooking_peak_start_default == time(17, 0)
        assert cfg.cooking_peak_end_default == time(18, 30)

    def test_deadline_times(self, minimal_env):
        cfg = load_config()
        assert cfg.dishwasher_hard_deadline == time(20, 0)
        assert cfg.washing_machine_hard_deadline == time(20, 0)
        assert cfg.dryer_hard_deadline == time(21, 0)

    def test_db_path_is_path_object(self, minimal_env):
        cfg = load_config()
        assert isinstance(cfg.db_path, Path)

    def test_override_via_env(self, monkeypatch, minimal_env):
        monkeypatch.setenv("HA_URL", "http://192.168.1.1:8123")
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("CYCLE_INTERVAL_S", "30")
        cfg = load_config()
        assert cfg.ha_url == "http://192.168.1.1:8123"
        assert cfg.log_level == "DEBUG"
        assert cfg.cycle_interval_s == 30

    def test_marstek_write_enabled_true(self, monkeypatch, minimal_env):
        monkeypatch.setenv("MARSTEK_WRITE_ENABLED", "true")
        cfg = load_config()
        assert cfg.marstek_write_enabled is True

    def test_marstek_write_enabled_false_variants(self, monkeypatch, minimal_env):
        for val in ("false", "0", "no", "False"):
            monkeypatch.setenv("MARSTEK_WRITE_ENABLED", val)
            cfg = load_config()
            assert cfg.marstek_write_enabled is False

    def test_missing_ha_url_raises(self, monkeypatch):
        monkeypatch.delenv("HA_URL", raising=False)
        monkeypatch.setenv("HA_TOKEN", "tok")
        monkeypatch.setenv("NOTIFICATION_DEVICE", "dev")
        with pytest.raises(ConfigError, match="HA_URL"):
            load_config()

    def test_missing_ha_token_raises(self, monkeypatch):
        monkeypatch.setenv("HA_URL", "http://localhost:8123")
        monkeypatch.delenv("HA_TOKEN", raising=False)
        monkeypatch.setenv("NOTIFICATION_DEVICE", "dev")
        with pytest.raises(ConfigError, match="HA_TOKEN"):
            load_config()

    def test_config_is_immutable(self, minimal_env):
        cfg = load_config()
        with pytest.raises((AttributeError, TypeError)):
            cfg.ha_url = "changed"  # type: ignore

    def test_intelligence_thresholds(self, minimal_env):
        cfg = load_config()
        assert cfg.thermal_model_min_samples == 336
        assert cfg.pattern_learner_min_days_basic == 14
        assert cfg.pattern_learner_min_days_seasonal == 90
        assert cfg.pv_calibration_min_days == 30
        assert cfg.thermal_r2_upgrade_threshold == pytest.approx(0.85)
