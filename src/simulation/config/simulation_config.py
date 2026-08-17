"""Simulation configuration system for Phase 03.3 Enterprise Transaction Simulation Engine.

Provides scale profiles (small, medium, large, enterprise), random seeds, date ranges,
multi-currency defaults, and anomaly injection controls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.simulation.domain.enums import Currency, ScaleProfile


@dataclass
class ScaleTarget:
    """Target record counts for a given scale profile."""

    employees: int
    customers: int
    suppliers: int
    products: int
    warehouses: int
    purchase_orders: int
    sales_orders: int
    expense_claims: int
    payroll_periods: int
    fixed_assets: int


# Built-in scale profiles
SCALE_PROFILES: dict[ScaleProfile, ScaleTarget] = {
    ScaleProfile.SMALL: ScaleTarget(
        employees=50,
        customers=100,
        suppliers=30,
        products=100,
        warehouses=2,
        purchase_orders=200,
        sales_orders=300,
        expense_claims=150,
        payroll_periods=12,
        fixed_assets=40,
    ),
    ScaleProfile.MEDIUM: ScaleTarget(
        employees=250,
        customers=500,
        suppliers=100,
        products=500,
        warehouses=5,
        purchase_orders=1000,
        sales_orders=1500,
        expense_claims=800,
        payroll_periods=24,
        fixed_assets=150,
    ),
    ScaleProfile.LARGE: ScaleTarget(
        employees=1000,
        customers=2500,
        suppliers=300,
        products=2000,
        warehouses=10,
        purchase_orders=5000,
        sales_orders=8000,
        expense_claims=4000,
        payroll_periods=24,
        fixed_assets=600,
    ),
    ScaleProfile.ENTERPRISE: ScaleTarget(
        employees=5000,
        customers=10000,
        suppliers=1000,
        products=10000,
        warehouses=25,
        purchase_orders=25000,
        sales_orders=40000,
        expense_claims=20000,
        payroll_periods=36,
        fixed_assets=3000,
    ),
}


@dataclass
class SimulationConfig:
    """Master configuration for transaction simulation execution."""

    scale_profile: ScaleProfile = ScaleProfile.SMALL
    start_date: datetime = field(default_factory=lambda: datetime(2025, 1, 1))
    end_date: datetime = field(default_factory=lambda: datetime(2025, 12, 31))
    seed: int = 42
    base_currency: Currency = Currency.USD
    enabled_currencies: list[Currency] = field(
        default_factory=lambda: [Currency.USD, Currency.EUR, Currency.GBP, Currency.BRL]
    )
    enable_anomaly_injection: bool = True
    overall_anomaly_rate: float = 0.05
    per_domain_anomaly_rates: dict[str, float] = field(
        default_factory=lambda: {
            "P2P": 0.04,
            "O2C": 0.03,
            "INVENTORY": 0.02,
            "EXPENSE": 0.05,
            "PAYROLL": 0.02,
            "ASSET": 0.03,
        }
    )
    export_csv: bool = True
    export_parquet: bool = True
    export_postgres_sql: bool = True
    batch_size: int = 500

    def get_scale_target(self) -> ScaleTarget:
        """Resolve scale target based on configured profile."""
        return SCALE_PROFILES.get(self.scale_profile, SCALE_PROFILES[ScaleProfile.SMALL])


__all__ = ["ScaleTarget", "SCALE_PROFILES", "SimulationConfig"]
