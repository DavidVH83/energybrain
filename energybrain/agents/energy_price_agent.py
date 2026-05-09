"""EnergyPriceAgent — provides current and hourly energy prices.

Two modes (CONTRACT_TYPE env var):
  static  — Fixed Eneco contract prices (current). Default.
  dynamic — ENTSO-E day-ahead prices (ready to activate, not yet deployed).

Static mode:
  Import: ~€0.25/kWh (fixed)
  Export: ~€0.036/kWh (BELPEX monthly avg formula)
  No cheap/expensive hours since all hours cost the same.

Dynamic mode:
  Cheap hours: below CHEAP_HOUR_THRESHOLD_PCT% of daily average
  Expensive hours: above EXPENSIVE_HOUR_THRESHOLD_PCT% of daily average
  Activation: set CONTRACT_TYPE=dynamic + ENTSOE_API_KEY in .env
"""
from __future__ import annotations

from energybrain.config import Config
from energybrain.models import EnergyPrice
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)


class EnergyPriceAgent:
    """Returns current and hourly energy prices from static or dynamic source."""

    AGENT_NAME = "energy_price_agent"

    def __init__(self, config: Config) -> None:
        self._config = config
        self._log = get_logger(self.AGENT_NAME)

    async def collect(self) -> EnergyPrice:
        """Return EnergyPrice for the current contract type."""
        if self._config.contract_type == "dynamic":
            return await self._collect_dynamic()
        return self._collect_static()

    def _collect_static(self) -> EnergyPrice:
        import_price = self._config.static_import_price_eur_kwh
        export_price = self._config.static_export_price_eur_kwh

        self._log.debug(
            "energy_price_static",
            import_eur_kwh=import_price,
            export_eur_kwh=export_price,
        )
        return EnergyPrice(
            current_import_eur_kwh=import_price,
            current_export_eur_kwh=export_price,
            hourly_import_prices=[import_price] * 24,
            cheap_hours=[],
            expensive_hours=[],
        )

    async def _collect_dynamic(self) -> EnergyPrice:
        """Fetch ENTSO-E day-ahead prices. Not yet activated.

        Falls back to static prices until the ENTSO-E integration is built.
        Activation requires CONTRACT_TYPE=dynamic and ENTSOE_API_KEY in .env.
        """
        self._log.warning(
            "dynamic_pricing_not_implemented",
            fallback="static",
            reason="ENTSO-E integration pending",
        )
        return self._collect_static()

    def _compute_cheap_expensive_hours(
        self,
        hourly_prices: list[float],
    ) -> tuple[list[int], list[int]]:
        """Classify hours as cheap or expensive relative to daily average.

        Used in dynamic mode once ENTSO-E prices are available.
        """
        if not hourly_prices:
            return [], []
        avg = sum(hourly_prices) / len(hourly_prices)
        cheap_threshold = avg * self._config.cheap_hour_threshold_pct / 100.0
        expensive_threshold = avg * self._config.expensive_hour_threshold_pct / 100.0
        cheap = [i for i, p in enumerate(hourly_prices) if p <= cheap_threshold]
        expensive = [i for i, p in enumerate(hourly_prices) if p >= expensive_threshold]
        return cheap, expensive
