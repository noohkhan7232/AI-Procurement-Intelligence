"""Multi-Currency Engine for Phase 03.3 Enterprise Transaction Simulation Engine.

Generates realistic exchange rates and currency conversion calculations relative to base currency.
"""

from __future__ import annotations

from datetime import datetime
import random

from src.simulation.config.simulation_config import SimulationConfig
from src.simulation.domain.enums import Currency


class CurrencyEngine:
    """Handles multi-currency conversions and exchange rate lookup matrices."""

    BASE_EXCHANGE_RATES: dict[Currency, float] = {
        Currency.USD: 1.0,
        Currency.EUR: 0.92,
        Currency.GBP: 0.79,
        Currency.BRL: 5.15,
        Currency.JPY: 155.0,
        Currency.CAD: 1.36,
        Currency.AUD: 1.51,
    }

    def __init__(self, config: SimulationConfig) -> None:
        """Initialize currency engine."""
        self._config = config
        self._base_currency = config.base_currency
        random.seed(config.seed)

    def get_exchange_rate(self, currency: Currency, dt: datetime | None = None) -> float:
        """Get exchange rate for target currency relative to base USD with realistic fluctuation."""
        if currency == self._base_currency:
            return 1.0

        base_rate = self.BASE_EXCHANGE_RATES.get(currency, 1.0)
        # Add small +/- 1.5% random daily market fluctuation
        fluctuation = random.uniform(-0.015, 0.015)
        return round(base_rate * (1.0 + fluctuation), 4)

    def convert_to_base(self, amount: float, from_currency: Currency, dt: datetime | None = None) -> tuple[float, float]:
        """Convert transaction amount to base currency (USD). Returns (base_amount, rate_used)."""
        if from_currency == self._base_currency:
            return amount, 1.0

        rate = self.get_exchange_rate(from_currency, dt)
        base_amount = round(amount / rate, 2) if rate > 0 else amount
        return base_amount, rate


__all__ = ["CurrencyEngine"]
