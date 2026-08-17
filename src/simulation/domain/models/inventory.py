"""Inventory domain models for Phase 03.3 Enterprise Transaction Simulation Engine.

Covers: Inventory Receipt, Issue, Transfer, Adjustment, Stock Reservation,
Release, Cycle Count, Reconciliation, Write-Off, Valuation Event.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.simulation.domain.enums import TransactionState
from src.simulation.domain.models.base import AuditEvidence


@dataclass
class InventoryMovement:
    """Core inventory movement ledger record."""

    movement_id: str
    movement_number: str
    movement_type: str  # RECEIPT, ISSUE, TRANSFER_OUT, TRANSFER_IN, ADJUSTMENT, WRITE_OFF
    product_id: str
    warehouse_id: str
    source_warehouse_id: str | None
    destination_warehouse_id: str | None
    quantity: float
    unit_cost: float
    total_value: float
    movement_date: datetime
    reason_code: str
    reference_document_id: str | None
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class StockReservation:
    """Stock Reservation for sales order / production."""

    reservation_id: str
    product_id: str
    warehouse_id: str
    reference_document_id: str
    quantity_reserved: float
    reservation_date: datetime
    expiration_date: datetime
    state: TransactionState
    correlation_id: str


@dataclass
class CycleCount:
    """Cycle Count physical inventory verification."""

    count_id: str
    count_number: str
    warehouse_id: str
    count_date: datetime
    counted_by_id: str
    product_id: str
    system_quantity: float
    counted_quantity: float
    variance_quantity: float
    variance_value: float
    adjustment_applied: bool
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class InventoryWriteOff:
    """Inventory Write-Off for damaged/expired stock."""

    write_off_id: str
    write_off_number: str
    warehouse_id: str
    product_id: str
    quantity: float
    unit_cost: float
    total_loss_value: float
    reason_code: str
    write_off_date: datetime
    approved_by_id: str
    state: TransactionState
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class InventoryValuationEvent:
    """Inventory valuation snapshot calculation."""

    valuation_id: str
    valuation_date: datetime
    warehouse_id: str
    product_id: str
    ending_quantity: float
    unit_cost_method: str  # FIFO, MOVING_AVERAGE
    total_inventory_value: float
    correlation_id: str


__all__ = [
    "InventoryMovement",
    "StockReservation",
    "CycleCount",
    "InventoryWriteOff",
    "InventoryValuationEvent",
]
