"""Business process rule configuration for Phase 03.3 Enterprise Transaction Simulation Engine.

Defines thresholds, tolerances, approval policies, and business rules across P2P, O2C,
Inventory, Expense, Payroll, and Asset processes.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class P2PProcessConfig:
    """Procure-to-Pay business process configuration."""

    price_variance_tolerance_pct: float = 2.0  # 2% price variance tolerance for 3-way match
    quantity_variance_tolerance_pct: float = 0.0  # 0% quantity variance tolerance
    approval_threshold_tier1: float = 1000.0  # Manager approval
    approval_threshold_tier2: float = 10000.0  # Director approval
    approval_threshold_tier3: float = 50000.0  # VP approval
    allow_partial_payments: bool = True
    payment_terms_days_default: int = 30


@dataclass
class O2CProcessConfig:
    """Order-to-Cash business process configuration."""

    credit_limit_check: bool = True
    default_payment_terms_days: int = 30
    allow_partial_shipment: bool = True
    max_return_window_days: int = 30


@dataclass
class InventoryProcessConfig:
    """Inventory process configuration."""

    prevent_negative_inventory: bool = True
    valuation_method: str = "FIFO"  # FIFO, MOVING_AVERAGE
    holding_cost_rate_annual: float = 0.15


@dataclass
class ExpenseProcessConfig:
    """Expense-to-Reimbursement process configuration."""

    receipt_required_threshold: float = 25.0  # Receipts required > $25
    per_diem_daily_rate: float = 75.0
    manager_approval_limit: float = 1500.0


@dataclass
class PayrollProcessConfig:
    """Hire-to-Payroll process configuration."""

    overtime_multiplier: float = 1.5
    tax_withholding_rate_avg: float = 0.22
    pension_contribution_rate: float = 0.05


@dataclass
class AssetProcessConfig:
    """Asset Lifecycle process configuration."""

    default_useful_life_years: int = 5
    default_salvage_value_pct: float = 0.10
    capitalization_threshold: float = 2500.0


@dataclass
class MasterProcessConfig:
    """Aggregate business process configuration."""

    p2p: P2PProcessConfig = field(default_factory=P2PProcessConfig)
    o2c: O2CProcessConfig = field(default_factory=O2CProcessConfig)
    inventory: InventoryProcessConfig = field(default_factory=InventoryProcessConfig)
    expense: ExpenseProcessConfig = field(default_factory=ExpenseProcessConfig)
    payroll: PayrollProcessConfig = field(default_factory=PayrollProcessConfig)
    asset: AssetProcessConfig = field(default_factory=AssetProcessConfig)


__all__ = [
    "P2PProcessConfig",
    "O2CProcessConfig",
    "InventoryProcessConfig",
    "ExpenseProcessConfig",
    "PayrollProcessConfig",
    "AssetProcessConfig",
    "MasterProcessConfig",
]
