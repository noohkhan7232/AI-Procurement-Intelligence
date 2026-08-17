"""Procure-to-Pay (P2P) Process Simulator for Phase 03.3 Enterprise Transaction Simulation Engine.

Orchestrates end-to-end P2P workflows: Requisition -> PO -> Goods Receipt -> Invoice ->
3-Way Match -> Approval -> Payment -> Allocation -> Accounting entries & Anomaly Injection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import random
import uuid

from src.simulation.config.process_config import P2PProcessConfig
from src.simulation.config.simulation_config import SimulationConfig
from src.simulation.domain.enums import AnomalySeverity, AnomalyType, Currency, EventType, TransactionState
from src.simulation.domain.models import (
    AuditEvidence,
    CorrelationContext,
    GoodsReceipt,
    GoodsReceiptItem,
    Payment,
    PaymentAllocation,
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseRequisition,
    PurchaseRequisitionItem,
    PurchaseReturn,
    SupplierCreditNote,
    SupplierInvoice,
    SupplierInvoiceItem,
    ThreeWayMatchResult,
)
from src.simulation.engines import (
    AccountingTransactionEngine,
    AnomalyEngine,
    ApprovalWorkflowEngine,
    BusinessEventEngine,
    CurrencyEngine,
    TemporalEngine,
)
from src.simulation.master_data import MasterDataPool
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class P2PProcessResults:
    """Container for all entities generated in a P2P simulation run."""

    requisitions: list[PurchaseRequisition] = field(default_factory=list)
    purchase_orders: list[PurchaseOrder] = field(default_factory=list)
    goods_receipts: list[GoodsReceipt] = field(default_factory=list)
    invoices: list[SupplierInvoice] = field(default_factory=list)
    match_results: list[ThreeWayMatchResult] = field(default_factory=list)
    payments: list[Payment] = field(default_factory=list)
    allocations: list[PaymentAllocation] = field(default_factory=list)
    purchase_returns: list[PurchaseReturn] = field(default_factory=list)
    credit_notes: list[SupplierCreditNote] = field(default_factory=list)


class ProcureToPaySimulator:
    """End-to-end P2P process simulation generator."""

    def __init__(
        self,
        config: SimulationConfig,
        p2p_config: P2PProcessConfig,
        master_pool: MasterDataPool,
        temporal_engine: TemporalEngine,
        currency_engine: CurrencyEngine,
        event_engine: BusinessEventEngine,
        approval_engine: ApprovalWorkflowEngine,
        accounting_engine: AccountingTransactionEngine,
        anomaly_engine: AnomalyEngine,
    ) -> None:
        """Initialize P2P simulator with collaborators."""
        self._config = config
        self._p2p_config = p2p_config
        self._master = master_pool
        self._temporal = temporal_engine
        self._currency = currency_engine
        self._event = event_engine
        self._approval = approval_engine
        self._accounting = accounting_engine
        self._anomaly = anomaly_engine
        random.seed(config.seed)

    def simulate(self, target_orders: int) -> P2PProcessResults:
        """Execute P2P process simulation producing target PO volume."""
        results = P2PProcessResults()
        logger.info("Executing Procure-to-Pay Process Simulation (Target POs: %d)...", target_orders)

        for i in range(1, target_orders + 1):
            correlation = CorrelationContext(process_type="P2P")
            curr = random.choice(self._config.enabled_currencies)
            ex_rate = self._currency.get_exchange_rate(curr)

            # 1. Purchase Requisition
            req_date = self._temporal.generate_random_timestamp(prefer_period_end=True)
            requester = random.choice(self._master.employees)
            supplier = random.choice(self._master.suppliers)

            req = self._generate_requisition(i, requester, req_date, curr, correlation)
            results.requisitions.append(req)

            # 2. Purchase Order
            po_date = self._temporal.add_process_delay(req_date, min_hours=2, max_hours=24)
            buyer = random.choice([e for e in self._master.employees if e.department_id == "DEP-PROC"] or self._master.employees)

            # Check Anomaly: Split Purchase
            if self._anomaly.should_inject("P2P") and random.random() < 0.1:
                # Split single high value into two POs
                self._anomaly.record_anomaly(
                    anomaly_type=AnomalyType.SPLIT_PURCHASE,
                    entity_type="PURCHASE_ORDER",
                    entity_id=f"PO-{i:06d}",
                    description=f"Purchase split into multiple POs to circumvent approval limit",
                    injection_rule="SPLIT_PO_UNDER_THRESHOLD",
                    expected_detection_rule="DETECT_SPLIT_POS_SAME_SUPPLIER_DAY",
                    correlation_id=correlation.correlation_id,
                )

            po = self._generate_purchase_order(i, req, supplier, buyer, po_date, curr, ex_rate, correlation)
            results.purchase_orders.append(po)

            # 3. Goods Receipt
            gr_date = self._temporal.add_process_delay(po_date, min_hours=12, max_hours=120)
            warehouse = random.choice(self._master.warehouses)
            receiver = random.choice(self._master.employees)

            gr = self._generate_goods_receipt(i, po, supplier, warehouse, receiver, gr_date, correlation)
            results.goods_receipts.append(gr)

            # 4. Supplier Invoice
            inv_date = self._temporal.add_process_delay(gr_date, min_hours=4, max_hours=48)
            force_amount_mismatch = self._anomaly.should_inject("P2P") and random.random() < 0.15

            if force_amount_mismatch:
                self._anomaly.record_anomaly(
                    anomaly_type=AnomalyType.PO_INVOICE_MISMATCH,
                    entity_type="SUPPLIER_INVOICE",
                    entity_id=f"INV-{i:06d}",
                    description=f"Supplier invoice amount higher than PO/GR amount",
                    injection_rule="INJECT_5_PCT_PRICE_INCREASE",
                    expected_detection_rule="DETECT_3WAY_MATCH_VARIANCE_EXCEEDED",
                    correlation_id=correlation.correlation_id,
                )

            invoice = self._generate_supplier_invoice(i, po, gr, supplier, inv_date, curr, correlation, force_amount_mismatch)
            results.invoices.append(invoice)

            # Anomaly: Duplicate Invoice
            if self._anomaly.should_inject("P2P") and random.random() < 0.05:
                dup_inv = self._generate_supplier_invoice(i * 10000, po, gr, supplier, inv_date, curr, correlation, False)
                dup_inv.invoice_number = invoice.invoice_number  # Duplicate invoice number
                results.invoices.append(dup_inv)
                self._anomaly.record_anomaly(
                    anomaly_type=AnomalyType.DUPLICATE_INVOICE,
                    entity_type="SUPPLIER_INVOICE",
                    entity_id=dup_inv.invoice_id,
                    description=f"Duplicate invoice submitted with identical invoice number {invoice.invoice_number}",
                    injection_rule="DUPLICATE_INVOICE_NUMBER",
                    expected_detection_rule="DETECT_DUPLICATE_INVOICE_NUMBERS",
                    correlation_id=correlation.correlation_id,
                )

            # 5. 3-Way Match Validation
            match_res = self._evaluate_three_way_match(invoice, po, gr)
            results.match_results.append(match_res)

            # 6. Invoice Approval
            force_unauth = self._anomaly.should_inject("P2P") and random.random() < 0.1
            if force_unauth:
                self._anomaly.record_anomaly(
                    anomaly_type=AnomalyType.UNAUTHORIZED_APPROVAL,
                    entity_type="INVOICE_APPROVAL",
                    entity_id=invoice.invoice_id,
                    description="Invoice approved by approver exceeding limit",
                    injection_rule="FORCE_LOW_LEVEL_APPROVER",
                    expected_detection_rule="DETECT_APPROVER_LIMIT_VIOLATION",
                    correlation_id=correlation.correlation_id,
                )

            appr_req = self._approval.process_approval_request(
                document_type="SUPPLIER_INVOICE",
                document_id=invoice.invoice_id,
                requested_by_id=buyer.employee_id,
                amount=invoice.total_amount,
                request_date=inv_date,
                correlation_id=correlation.correlation_id,
                force_unauthorized=force_unauth,
            )

            if appr_req.state == TransactionState.APPROVED and match_res.matched:
                invoice.state = TransactionState.APPROVED
                self._event.create_event(
                    EventType.INVOICE_APPROVED, "SUPPLIER_INVOICE", invoice.invoice_id, correlation.correlation_id
                )

                # Post Invoice Accounting Entry (Debit COGS/Inventory, Credit Accounts Payable)
                self._accounting.create_journal_entry(
                    source_document_type="P2P_INVOICE",
                    source_document_id=invoice.invoice_id,
                    posting_date=inv_date,
                    currency=curr,
                    exchange_rate=ex_rate,
                    debit_entries=[("120000", invoice.subtotal, "Inventory Receipt")],
                    credit_entries=[("200000", invoice.total_amount, "Accounts Payable")],
                    correlation_id=correlation.correlation_id,
                )

                # 7. Payment
                payment_date = self._temporal.add_process_delay(inv_date, min_hours=24, max_hours=720)
                pmt = self._generate_payment(i, invoice, supplier, payment_date, curr, correlation)
                results.payments.append(pmt)
                results.allocations.extend(pmt.allocations)

                invoice.state = TransactionState.COMPLETED
                invoice.paid_amount = invoice.total_amount

                # Post Payment Accounting Entry (Debit AP, Credit Cash)
                self._accounting.create_journal_entry(
                    source_document_type="P2P_PAYMENT",
                    source_document_id=pmt.payment_id,
                    posting_date=payment_date,
                    currency=curr,
                    exchange_rate=ex_rate,
                    debit_entries=[("200000", pmt.amount, "AP Disbursal")],
                    credit_entries=[("100000", pmt.amount, "Cash Outflow")],
                    correlation_id=correlation.correlation_id,
                )

        logger.info("Procure-to-Pay Simulation Complete: %d POs, %d Payments generated.", len(results.purchase_orders), len(results.payments))
        return results

    def _generate_requisition(
        self, idx: int, requester: Any, req_date: datetime, curr: Currency, correlation: CorrelationContext
    ) -> PurchaseRequisition:
        req_id = f"REQ-{idx:06d}"
        items = []
        num_items = random.randint(1, 4)
        total = 0.0

        for item_idx in range(1, num_items + 1):
            prod = random.choice(self._master.products)
            qty = float(random.randint(5, 100))
            price = prod.cost_price
            line_tot = round(qty * price, 2)
            total += line_tot

            items.append(
                PurchaseRequisitionItem(
                    item_id=f"{req_id}-LINE-{item_idx}",
                    requisition_id=req_id,
                    product_id=prod.product_id,
                    product_name=prod.product_name,
                    quantity=qty,
                    unit_of_measure="EA",
                    estimated_unit_price=price,
                    estimated_total_price=line_tot,
                )
            )

        correlation.add_step("REQUISITION", req_id)
        self._event.create_event(EventType.PURCHASE_REQUISITION_CREATED, "PURCHASE_REQUISITION", req_id, correlation.correlation_id, req_date)

        return PurchaseRequisition(
            requisition_id=req_id,
            requisition_number=f"PR-NUM-{idx:06d}",
            requester_id=requester.employee_id,
            department_id=requester.department_id,
            business_unit_id="BU-100",
            creation_date=req_date,
            state=TransactionState.APPROVED,
            total_amount=round(total, 2),
            currency=curr,
            correlation_id=correlation.correlation_id,
            items=items,
            audit_evidence=AuditEvidence(transaction_id=req_id, created_by=requester.employee_id, created_timestamp=req_date, correlation_id=correlation.correlation_id),
        )

    def _generate_purchase_order(
        self, idx: int, req: PurchaseRequisition, supplier: Any, buyer: Any, po_date: datetime, curr: Currency, ex_rate: float, correlation: CorrelationContext
    ) -> PurchaseOrder:
        po_id = f"PO-{idx:06d}"
        items = []
        total = 0.0

        for req_item in req.items:
            qty = req_item.quantity
            price = req_item.estimated_unit_price
            line_tot = round(qty * price, 2)
            tax = round(line_tot * 0.08, 2)
            total += line_tot + tax

            items.append(
                PurchaseOrderItem(
                    item_id=f"{po_id}-LINE-{req_item.item_id[-1]}",
                    po_id=po_id,
                    requisition_item_id=req_item.item_id,
                    product_id=req_item.product_id,
                    product_name=req_item.product_name,
                    quantity=qty,
                    unit_price=price,
                    total_price=line_tot,
                    tax_amount=tax,
                    delivery_date=po_date + timedelta(days=7),
                )
            )

        correlation.add_step("PURCHASE_ORDER", po_id)
        self._event.create_event(EventType.PURCHASE_ORDER_CREATED, "PURCHASE_ORDER", po_id, correlation.correlation_id, po_date)

        return PurchaseOrder(
            po_id=po_id,
            po_number=f"PO-NUM-{idx:06d}",
            requisition_id=req.requisition_id,
            supplier_id=supplier.supplier_id,
            buyer_id=buyer.employee_id,
            company_code="1000",
            business_unit_id="BU-100",
            po_date=po_date,
            state=TransactionState.APPROVED,
            total_amount=round(total, 2),
            currency=curr,
            exchange_rate=ex_rate,
            correlation_id=correlation.correlation_id,
            items=items,
            audit_evidence=AuditEvidence(transaction_id=po_id, created_by=buyer.employee_id, created_timestamp=po_date, correlation_id=correlation.correlation_id),
        )

    def _generate_goods_receipt(
        self, idx: int, po: PurchaseOrder, supplier: Any, warehouse: Any, receiver: Any, gr_date: datetime, correlation: CorrelationContext
    ) -> GoodsReceipt:
        receipt_id = f"GR-{idx:06d}"
        items = []

        for po_item in po.items:
            items.append(
                GoodsReceiptItem(
                    receipt_item_id=f"{receipt_id}-LINE-{po_item.item_id[-1]}",
                    receipt_id=receipt_id,
                    po_item_id=po_item.item_id,
                    product_id=po_item.product_id,
                    quantity_ordered=po_item.quantity,
                    quantity_received=po_item.quantity,
                    quantity_accepted=po_item.quantity,
                    quantity_rejected=0.0,
                    unit_price=po_item.unit_price,
                    total_received_amount=po_item.total_price,
                )
            )

        correlation.add_step("GOODS_RECEIPT", receipt_id)
        self._event.create_event(EventType.GOODS_RECEIVED, "GOODS_RECEIPT", receipt_id, correlation.correlation_id, gr_date)

        return GoodsReceipt(
            receipt_id=receipt_id,
            receipt_number=f"GR-NUM-{idx:06d}",
            po_id=po.po_id,
            supplier_id=supplier.supplier_id,
            warehouse_id=warehouse.warehouse_id,
            received_by_id=receiver.employee_id,
            receipt_date=gr_date,
            state=TransactionState.COMPLETED,
            correlation_id=correlation.correlation_id,
            items=items,
            audit_evidence=AuditEvidence(transaction_id=receipt_id, created_by=receiver.employee_id, created_timestamp=gr_date, correlation_id=correlation.correlation_id),
        )

    def _generate_supplier_invoice(
        self, idx: int, po: PurchaseOrder, gr: GoodsReceipt, supplier: Any, inv_date: datetime, curr: Currency, correlation: CorrelationContext, force_mismatch: bool
    ) -> SupplierInvoice:
        inv_id = f"INV-{idx:06d}"
        items = []
        subtotal = 0.0

        for po_item in po.items:
            unit_price = po_item.unit_price * (1.05 if force_mismatch else 1.0)
            line_tot = round(po_item.quantity * unit_price, 2)
            subtotal += line_tot

            items.append(
                SupplierInvoiceItem(
                    invoice_item_id=f"{inv_id}-LINE-{po_item.item_id[-1]}",
                    invoice_id=inv_id,
                    po_item_id=po_item.item_id,
                    product_id=po_item.product_id,
                    quantity_invoiced=po_item.quantity,
                    unit_price=unit_price,
                    line_total=line_tot,
                    tax_amount=round(line_tot * 0.08, 2),
                )
            )

        tax = round(subtotal * 0.08, 2)
        total = subtotal + tax

        correlation.add_step("SUPPLIER_INVOICE", inv_id)
        self._event.create_event(EventType.INVOICE_CREATED, "SUPPLIER_INVOICE", inv_id, correlation.correlation_id, inv_date)

        return SupplierInvoice(
            invoice_id=inv_id,
            invoice_number=f"INV-NUM-{idx:06d}",
            supplier_id=supplier.supplier_id,
            po_id=po.po_id,
            receipt_id=gr.receipt_id,
            invoice_date=inv_date,
            due_date=inv_date + timedelta(days=30),
            state=TransactionState.PENDING_APPROVAL,
            subtotal=round(subtotal, 2),
            tax_amount=tax,
            total_amount=round(total, 2),
            paid_amount=0.0,
            currency=curr,
            correlation_id=correlation.correlation_id,
            items=items,
            audit_evidence=AuditEvidence(transaction_id=inv_id, created_by="SUPPLIER_PORTAL", created_timestamp=inv_date, correlation_id=correlation.correlation_id),
        )

    def _evaluate_three_way_match(self, inv: SupplierInvoice, po: PurchaseOrder, gr: GoodsReceipt) -> ThreeWayMatchResult:
        variance = abs(inv.total_amount - po.total_amount)
        var_pct = (variance / po.total_amount) * 100.0 if po.total_amount > 0 else 0.0
        exceeded = var_pct > self._p2p_config.price_variance_tolerance_pct

        return ThreeWayMatchResult(
            match_id=f"MATCH-{inv.invoice_id}",
            invoice_id=inv.invoice_id,
            po_id=po.po_id,
            receipt_id=gr.receipt_id,
            matched=(not exceeded),
            po_amount=po.total_amount,
            receipt_amount=po.total_amount,
            invoice_amount=inv.total_amount,
            amount_variance=round(variance, 2),
            quantity_variance=0.0,
            variance_percentage=round(var_pct, 2),
            tolerance_exceeded=exceeded,
            match_status="PASS" if not exceeded else "FAIL_PRICE_VARIANCE",
            timestamp=datetime.now(),
        )

    def _generate_payment(
        self, idx: int, inv: SupplierInvoice, supplier: Any, pmt_date: datetime, curr: Currency, correlation: CorrelationContext
    ) -> Payment:
        pmt_id = f"PMT-{idx:06d}"
        alloc = PaymentAllocation(
            allocation_id=f"ALLOC-{pmt_id}-{inv.invoice_id}",
            payment_id=pmt_id,
            invoice_id=inv.invoice_id,
            allocated_amount=inv.total_amount,
            allocation_date=pmt_date,
        )

        correlation.add_step("PAYMENT", pmt_id)
        self._event.create_event(EventType.PAYMENT_RELEASED, "PAYMENT", pmt_id, correlation.correlation_id, pmt_date)

        return Payment(
            payment_id=pmt_id,
            payment_number=f"PMT-NUM-{idx:06d}",
            supplier_id=supplier.supplier_id,
            invoice_ids=[inv.invoice_id],
            payment_date=pmt_date,
            amount=inv.total_amount,
            payment_method="ACH_TRANSFER",
            bank_account_id="BANK-OPERATING-01",
            state=TransactionState.COMPLETED,
            currency=curr,
            correlation_id=correlation.correlation_id,
            allocations=[alloc],
            audit_evidence=AuditEvidence(transaction_id=pmt_id, created_by="TREASURY_ENGINE", created_timestamp=pmt_date, correlation_id=correlation.correlation_id),
        )


__all__ = ["P2PProcessResults", "ProcureToPaySimulator"]
