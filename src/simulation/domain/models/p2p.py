"""Procure-to-Pay (P2P) domain models for Phase 03.3 Enterprise Transaction Simulation Engine.

Covers: Purchase Requisition, PO, Goods Receipt, Supplier Invoice, 3-Way Match,
Invoice Approval, Payment, Payment Allocation, Purchase Return, Supplier Credit Note.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.simulation.domain.enums import Currency, TransactionState
from src.simulation.domain.models.base import AuditEvidence, BusinessEvent


@dataclass
class PurchaseRequisitionItem:
    """Line item in a Purchase Requisition."""

    item_id: str
    requisition_id: str
    product_id: str
    product_name: str
    quantity: float
    unit_of_measure: str
    estimated_unit_price: float
    estimated_total_price: float


@dataclass
class PurchaseRequisition:
    """Purchase Requisition header."""

    requisition_id: str
    requisition_number: str
    requester_id: str
    department_id: str
    business_unit_id: str
    creation_date: datetime
    state: TransactionState
    total_amount: float
    currency: Currency
    correlation_id: str
    items: list[PurchaseRequisitionItem] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class PurchaseOrderItem:
    """Line item in a Purchase Order."""

    item_id: str
    po_id: str
    requisition_item_id: str | None
    product_id: str
    product_name: str
    quantity: float
    unit_price: float
    total_price: float
    tax_amount: float
    delivery_date: datetime


@dataclass
class PurchaseOrder:
    """Purchase Order header."""

    po_id: str
    po_number: str
    requisition_id: str | None
    supplier_id: str
    buyer_id: str
    company_code: str
    business_unit_id: str
    po_date: datetime
    state: TransactionState
    total_amount: float
    currency: Currency
    exchange_rate: float
    correlation_id: str
    items: list[PurchaseOrderItem] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class GoodsReceiptItem:
    """Line item in a Goods Receipt."""

    receipt_item_id: str
    receipt_id: str
    po_item_id: str
    product_id: str
    quantity_ordered: float
    quantity_received: float
    quantity_accepted: float
    quantity_rejected: float
    unit_price: float
    total_received_amount: float


@dataclass
class GoodsReceipt:
    """Goods Receipt header."""

    receipt_id: str
    receipt_number: str
    po_id: str
    supplier_id: str
    warehouse_id: str
    received_by_id: str
    receipt_date: datetime
    state: TransactionState
    correlation_id: str
    items: list[GoodsReceiptItem] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class SupplierInvoiceItem:
    """Line item in a Supplier Invoice."""

    invoice_item_id: str
    invoice_id: str
    po_item_id: str
    product_id: str
    quantity_invoiced: float
    unit_price: float
    line_total: float
    tax_amount: float


@dataclass
class SupplierInvoice:
    """Supplier Invoice header."""

    invoice_id: str
    invoice_number: str
    supplier_id: str
    po_id: str
    receipt_id: str | None
    invoice_date: datetime
    due_date: datetime
    state: TransactionState
    subtotal: float
    tax_amount: float
    total_amount: float
    paid_amount: float
    currency: Currency
    correlation_id: str
    items: list[SupplierInvoiceItem] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class ThreeWayMatchResult:
    """Result of automated 3-way matching (PO vs GR vs Invoice)."""

    match_id: str
    invoice_id: str
    po_id: str
    receipt_id: str
    matched: bool
    po_amount: float
    receipt_amount: float
    invoice_amount: float
    amount_variance: float
    quantity_variance: float
    variance_percentage: float
    tolerance_exceeded: bool
    match_status: str
    timestamp: datetime


@dataclass
class PaymentAllocation:
    """Allocation of a payment against a specific invoice."""

    allocation_id: str
    payment_id: str
    invoice_id: str
    allocated_amount: float
    allocation_date: datetime


@dataclass
class Payment:
    """Payment record."""

    payment_id: str
    payment_number: str
    supplier_id: str
    invoice_ids: list[str]
    payment_date: datetime
    amount: float
    payment_method: str
    bank_account_id: str
    state: TransactionState
    currency: Currency
    correlation_id: str
    allocations: list[PaymentAllocation] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class PurchaseReturn:
    """Purchase Return for rejected/damaged goods."""

    return_id: str
    return_number: str
    receipt_id: str
    po_id: str
    supplier_id: str
    return_date: datetime
    reason_code: str
    total_refund_expected: float
    state: TransactionState
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class SupplierCreditNote:
    """Supplier Credit Note adjusting an invoice."""

    credit_note_id: str
    credit_note_number: str
    invoice_id: str
    supplier_id: str
    issue_date: datetime
    credit_amount: float
    reason: str
    state: TransactionState
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


__all__ = [
    "PurchaseRequisitionItem",
    "PurchaseRequisition",
    "PurchaseOrderItem",
    "PurchaseOrder",
    "GoodsReceiptItem",
    "GoodsReceipt",
    "SupplierInvoiceItem",
    "SupplierInvoice",
    "ThreeWayMatchResult",
    "PaymentAllocation",
    "Payment",
    "PurchaseReturn",
    "SupplierCreditNote",
]
