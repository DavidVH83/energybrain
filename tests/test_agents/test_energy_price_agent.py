"""Tests for energybrain.agents.energy_price_agent."""
import pytest

from energybrain.agents.energy_price_agent import EnergyPriceAgent
from energybrain.config import load_config


class TestEnergyPriceAgentStatic:
    async def test_static_returns_energy_price(self, minimal_env):
        agent = EnergyPriceAgent(load_config())
        price = await agent.collect()
        assert price is not None

    async def test_static_import_price_from_config(self, minimal_env):
        agent = EnergyPriceAgent(load_config())
        price = await agent.collect()
        assert price.current_import_eur_kwh == pytest.approx(0.25)

    async def test_static_export_price_from_config(self, minimal_env):
        agent = EnergyPriceAgent(load_config())
        price = await agent.collect()
        assert price.current_export_eur_kwh == pytest.approx(0.036)

    async def test_static_returns_24_hourly_prices(self, minimal_env):
        agent = EnergyPriceAgent(load_config())
        price = await agent.collect()
        assert len(price.hourly_import_prices) == 24

    async def test_static_all_hours_same_price(self, minimal_env):
        agent = EnergyPriceAgent(load_config())
        price = await agent.collect()
        assert all(p == pytest.approx(0.25) for p in price.hourly_import_prices)

    async def test_static_no_cheap_hours(self, minimal_env):
        agent = EnergyPriceAgent(load_config())
        price = await agent.collect()
        assert price.cheap_hours == []

    async def test_static_no_expensive_hours(self, minimal_env):
        agent = EnergyPriceAgent(load_config())
        price = await agent.collect()
        assert price.expensive_hours == []

    async def test_custom_import_price_via_env(self, minimal_env, monkeypatch):
        monkeypatch.setenv("STATIC_IMPORT_PRICE_EUR_KWH", "0.30")
        agent = EnergyPriceAgent(load_config())
        price = await agent.collect()
        assert price.current_import_eur_kwh == pytest.approx(0.30)


class TestEnergyPriceAgentDynamic:
    async def test_dynamic_falls_back_to_static(self, minimal_env, monkeypatch):
        monkeypatch.setenv("CONTRACT_TYPE", "dynamic")
        agent = EnergyPriceAgent(load_config())
        price = await agent.collect()
        # Falls back to static since ENTSO-E is not yet implemented
        assert price.current_import_eur_kwh == pytest.approx(0.25)


class TestComputeCheapExpensiveHours:
    def test_classifies_cheap_hours_below_threshold(self, minimal_env):
        agent = EnergyPriceAgent(load_config())  # cheap_threshold=70%, expensive=130%
        prices = [0.10] * 8 + [0.25] * 8 + [0.40] * 8  # avg = 0.25
        cheap, expensive = agent._compute_cheap_expensive_hours(prices)
        # avg = 0.25, cheap_threshold = 0.25 * 0.70 = 0.175 → hours 0-7 (0.10) are cheap
        assert 0 in cheap
        assert 8 not in cheap  # 0.25 is not below 0.175

    def test_classifies_expensive_hours_above_threshold(self, minimal_env):
        agent = EnergyPriceAgent(load_config())
        prices = [0.10] * 8 + [0.25] * 8 + [0.40] * 8  # avg = 0.25
        cheap, expensive = agent._compute_cheap_expensive_hours(prices)
        # expensive_threshold = 0.25 * 1.30 = 0.325 → hours 16-23 (0.40) are expensive
        assert 16 in expensive
        assert 0 not in expensive

    def test_empty_prices_returns_empty_lists(self, minimal_env):
        agent = EnergyPriceAgent(load_config())
        cheap, expensive = agent._compute_cheap_expensive_hours([])
        assert cheap == []
        assert expensive == []
