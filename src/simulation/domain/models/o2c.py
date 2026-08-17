"""Order-to-Cash (O2C) domain models for Phase 03.3 Enterprise Transaction Simulation Engine.

Covers: Sales Order, Sales Order Items, Confirmation, Shipment, Delivery,
Customer Invoice, Customer Payment, Payment Allocation, Sales Return, Refund.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.simulation.domain.enums import Currency, TransactionState
from src.simulation.domain.models.base import AuditEvidence


@dataclass
class SalesOrderItem:
    """Line item in a Sales Order."""

    item_id: str
    order_id: str
    product_id: str
    product_name: str
    quantity: float
    unit_price: float
    discount_amount: float
    tax_amount: float
    total_amount: float


@dataclass
class SalesOrder:
    """Sales Order header."""

    order_id: str
    order_number: str
    customer_id: str
    sales_rep_id: str
    business_unit_id: str
    order_date: datetime
    state: TransactionState
    subtotal: float
    discount_total: float
    tax_total: float
    total_amount: float
    currency: Currency
    correlation_id: str
    items: list[SalesOrderItem] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class OrderConfirmation:
    """Sales Order Confirmation event record."""

    confirmation_id: str
    order_id: str
    customer_id: str
    confirmation_date: datetime
    promised_delivery_date: datetime
    state: TransactionState
    correlation_id: str


@dataclass
class ShipmentItem:
    """Line item in a Shipment."""

    shipment_item_id: str
    shipment_id: str
    order_item_id: str
    product_id: str
    quantity_shipped: float


@dataclass
class Shipment:
    """Shipment header."""

    shipment_id: str
    shipment_number: str
    order_id: str
    warehouse_id: str
    carrier_code: str
    tracking_number: str
    shipment_date: datetime
    estimated_arrival_date: datetime
    state: TransactionState
    correlation_id: str
    items: list[ShipmentItem] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class Delivery:
    """Customer Delivery confirmation."""

    delivery_id: str
    delivery_number: str
    shipment_id: str
    order_id: str
    customer_id: str
    actual_delivery_date: datetime
    received_by_name: str
    delivery_status: str
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class CustomerInvoiceItem:
    """Line item in a Customer Invoice."""

    invoice_item_id: str
    invoice_id: str
    order_item_id: str
    product_id: str
    quantity_invoiced: float
    unit_price: float
    line_total: float


@dataclass
class CustomerInvoice:
    """Customer Invoice header."""

    invoice_id: str
    invoice_number: str
    order_id: str
    customer_id: str
    invoice_date: datetime
    due_date: datetime
    state: TransactionState
    subtotal: float
    tax_amount: float
    total_amount: float
    paid_amount: float
    currency: Currency
    correlation_id: str
    items: list[CustomerInvoiceItem] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class CustomerPaymentAllocation:
    """Allocation of customer payment against customer invoice."""

    allocation_id: str
    payment_id: str
    invoice_id: str
    allocated_amount: float
    allocation_date: datetime


@dataclass
class CustomerPayment:
    """Customer Payment record."""

    payment_id: str
    payment_number: str
    customer_id: str
    payment_date: datetime
    amount: float
    payment_method: str
    reference_number: str
    state: TransactionState
    currency: Currency
    correlation_id: str
    allocations: list[CustomerPaymentAllocation] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class SalesReturn:
    """Sales Return initiated by customer."""

    return_id: str
    return_number: str
    order_id: str
    customer_id: str
    return_date: datetime
    reason_code: str
    refund_amount_expected: float
    state: TransactionState
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class CustomerRefund:
    """Refund issued to customer."""

    refund_id: str
    refund_number: str
    return_id: str
    customer_id: str
    refund_date: datetime
    amount: float
    payment_method: str
    state: TransactionState
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


__all__ = [
    "SalesOrderItem",
    "SalesOrder",
    "OrderConfirmation",
    "ShipmentItem",
    "Shipment",
    "Delivery",
    "CustomerInvoiceItem",
    "CustomerInvoice",
    "CustomerPaymentAllocation",
    "CustomerPayment",
    "SalesReturn",
    "CustomerRefund",
]
