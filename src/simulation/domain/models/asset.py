"""Asset Lifecycle domain models for Phase 03.3 Enterprise Transaction Simulation Engine.

Covers: Asset Acquisition, Registration, Assignment, Transfer, Maintenance,
Depreciation, Disposal, Write-Off, Asset Audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.simulation.domain.enums import Currency, TransactionState
from src.simulation.domain.models.base import AuditEvidence


@dataclass
class AssetMaster:
    """Fixed Asset Master Record."""

    asset_id: str
    asset_number: str
    asset_name: str
    category_id: str
    category_name: str
    acquisition_date: datetime
    acquisition_cost: float
    salvage_value: float
    useful_life_years: int
    depreciation_method: str  # STRAIGHT_LINE, DECLINING_BALANCE
    current_book_value: float
    accumulated_depreciation: float
    assigned_employee_id: str | None
    location_id: str
    custodian_id: str
    state: TransactionState
    currency: Currency
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class AssetDepreciationEvent:
    """Monthly/Periodic depreciation calculation entry."""

    event_id: str
    asset_id: str
    period_date: datetime
    depreciation_amount: float
    accumulated_depreciation_after: float
    book_value_after: float
    journal_entry_id: str | None
    correlation_id: str


@dataclass
class AssetTransferRecord:
    """Record of asset relocation or custodian reassignment."""

    transfer_id: str
    asset_id: str
    transfer_date: datetime
    from_location_id: str
    to_location_id: str
    from_employee_id: str | None
    to_employee_id: str | None
    reason: str
    correlation_id: str


@dataclass
class AssetMaintenanceRecord:
    """Asset maintenance/repair activity log."""

    maintenance_id: str
    asset_id: str
    maintenance_date: datetime
    service_provider: str
    cost: float
    maintenance_type: str  # PREVENTIVE, CORRECTIVE
    description: str
    correlation_id: str


@dataclass
class AssetDisposalRecord:
    """Asset disposal or sale record."""

    disposal_id: str
    asset_id: str
    disposal_date: datetime
    disposal_type: str  # SALE, SCRAP, DONATION
    sale_amount: float
    book_value_at_disposal: float
    gain_or_loss_amount: float
    state: TransactionState
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


__all__ = [
    "AssetMaster",
    "AssetDepreciationEvent",
    "AssetTransferRecord",
    "AssetMaintenanceRecord",
    "AssetDisposalRecord",
]
