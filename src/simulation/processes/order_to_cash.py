"""Order-to-Cash (O2C) Process Simulator for Phase 03.3 Enterprise Transaction Simulation Engine.

Orchestrates end-to-end O2C workflows: Sales Order -> Confirmation -> Shipment -> Delivery ->
Customer Invoice -> Customer Payment -> Allocation -> Accounting Entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import random
import uuid

from src.simulation.config.process_config import O2CProcessConfig
from src.simulation.config.simulation_config import SimulationConfig
from src.simulation.domain.enums import Currency, EventType, TransactionState
from src.simulation.domain.models import (
    AuditEvidence,
    CorrelationContext,
    CustomerInvoice,
    CustomerInvoiceItem,
    CustomerPayment,
    CustomerPaymentAllocation,
    CustomerRefund,
    Delivery,
    OrderConfirmation,
    SalesOrder,
    SalesOrderItem,
    SalesReturn,
    Shipment,
    ShipmentItem,
)
from src.simulation.engines import (
    AccountingTransactionEngine,
    AnomalyEngine,
    BusinessEventEngine,
    CurrencyEngine,
    TemporalEngine,
)
from src.simulation.master_data import MasterDataPool
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class O2CProcessResults:
    """Container for all entities generated in an O2C simulation run."""

    sales_orders: list[SalesOrder] = field(default_factory=list)
    confirmations: list[OrderConfirmation] = field(default_factory=list)
    shipments: list[Shipment] = field(default_factory=list)
    deliveries: list[Delivery] = field(default_factory=list)
    invoices: list[CustomerInvoice] = field(default_factory=list)
    payments: list[CustomerPayment] = field(default_factory=list)
    allocations: list[CustomerPaymentAllocation] = field(default_factory=list)
    returns: list[SalesReturn] = field(default_factory=list)
    refunds: list[CustomerRefund] = field(default_factory=list)


class OrderToCashSimulator:
    """End-to-end O2C process simulation generator."""

    def __init__(
        self,
        config: SimulationConfig,
        o2c_config: O2CProcessConfig,
        master_pool: MasterDataPool,
        temporal_engine: TemporalEngine,
        currency_engine: CurrencyEngine,
        event_engine: BusinessEventEngine,
        accounting_engine: AccountingTransactionEngine,
        anomaly_engine: AnomalyEngine,
    ) -> None:
        """Initialize O2C simulator."""
        self._config = config
        self._o2c_config = o2c_config
        self._master = master_pool
        self._temporal = temporal_engine
        self._currency = currency_engine
        self._event = event_engine
        self._accounting = accounting_engine
        self._anomaly = anomaly_engine
        random.seed(config.seed)

    def simulate(self, target_orders: int) -> O2CProcessResults:
        """Execute O2C process simulation producing target sales order volume."""
        results = O2CProcessResults()
        logger.info("Executing Order-to-Cash Process Simulation (Target Orders: %d)...", target_orders)

        for i in range(1, target_orders + 1):
            correlation = CorrelationContext(process_type="O2C")
            curr = random.choice(self._config.enabled_currencies)
            ex_rate = self._currency.get_exchange_rate(curr)

            customer = random.choice(self._master.customers)
            sales_rep = random.choice([e for e in self._master.employees if e.department_id == "DEP-SALES"] or self._master.employees)
            order_date = self._temporal.generate_random_timestamp(prefer_period_end=True)

            # 1. Sales Order
            order = self._generate_sales_order(i, customer, sales_rep, order_date, curr, correlation)
            results.sales_orders.append(order)

            # 2. Order Confirmation
            conf_date = self._temporal.add_process_delay(order_date, min_hours=1, max_hours=6)
            conf = OrderConfirmation(
                confirmation_id=f"CONF-{order.order_id}",
                order_id=order.order_id,
                customer_id=customer.customer_id,
                confirmation_date=conf_date,
                promised_delivery_date=conf_date + timedelta(days=5),
                state=TransactionState.COMPLETED,
                correlation_id=correlation.correlation_id,
            )
            results.confirmations.append(conf)
            self._event.create_event(EventType.ORDER_CONFIRMED, "SALES_ORDER", order.order_id, correlation.correlation_id, conf_date)

            # 3. Shipment
            ship_date = self._temporal.add_process_delay(conf_date, min_hours=12, max_hours=48)
            wh = random.choice(self._master.warehouses)
            shipment = self._generate_shipment(i, order, wh, ship_date, correlation)
            results.shipments.append(shipment)

            # 4. Delivery
            deliv_date = self._temporal.add_process_delay(ship_date, min_hours=24, max_hours=72)
            delivery = Delivery(
                delivery_id=f"DELIV-{i:06d}",
                delivery_number=f"DELIV-NUM-{i:06d}",
                shipment_id=shipment.shipment_id,
                order_id=order.order_id,
                customer_id=customer.customer_id,
                actual_delivery_date=deliv_date,
                received_by_name=f"Sig {customer.customer_name[:10]}",
                delivery_status="DELIVERED",
                correlation_id=correlation.correlation_id,
                audit_evidence=AuditEvidence(transaction_id=f"DELIV-{i:06d}", created_timestamp=deliv_date, correlation_id=correlation.correlation_id),
            )
            results.deliveries.append(delivery)
            self._event.create_event(EventType.DELIVERY_COMPLETED, "DELIVERY", delivery.delivery_id, correlation.correlation_id, deliv_date)

            # 5. Customer Invoice
            inv_date = self._temporal.add_process_delay(deliv_date, min_hours=2, max_hours=24)
            invoice = self._generate_customer_invoice(i, order, customer, inv_date, curr, correlation)
            results.invoices.append(invoice)

            # Accounting Entry: Post Revenue & AR (Debit AR 110000, Credit Sales Revenue 400000)
            self._accounting.create_journal_entry(
                source_document_type="O2C_INVOICE",
                source_document_id=invoice.invoice_id,
                posting_date=inv_date,
                currency=curr,
                exchange_rate=ex_rate,
                debit_entries=[("110000", invoice.total_amount, "Accounts Receivable")],
                credit_entries=[("400000", invoice.subtotal, "Sales Revenue"), ("220000", invoice.tax_amount, "Sales Tax Payable")],
                correlation_id=correlation.correlation_id,
            )

            # 6. Customer Payment
            pmt_date = self._temporal.add_process_delay(inv_date, min_hours=48, max_hours=720)
            payment = self._generate_customer_payment(i, invoice, customer, pmt_date, curr, correlation)
            results.payments.append(payment)
            results.allocations.extend(payment.allocations)

            invoice.paid_amount = invoice.total_amount
            invoice.state = TransactionState.COMPLETED

            # Accounting Entry: Cash Receipt (Debit Cash 100000, Credit AR 110000)
            self._accounting.create_journal_entry(
                source_document_type="O2C_PAYMENT",
                source_document_id=payment.payment_id,
                posting_date=pmt_date,
                currency=curr,
                exchange_rate=ex_rate,
                debit_entries=[("100000", payment.amount, "Cash Receipt")],
                credit_entries=[("110000", payment.amount, "AR Settlement")],
                correlation_id=correlation.correlation_id,
            )

        logger.info("Order-to-Cash Simulation Complete: %d Sales Orders, %d Payments generated.", len(results.sales_orders), len(results.payments))
        return results

    def _generate_sales_order(
        self, idx: int, customer: Any, sales_rep: Any, order_date: datetime, curr: Currency, correlation: CorrelationContext
    ) -> SalesOrder:
        order_id = f"SO-{idx:06d}"
        items = []
        subtotal = 0.0

        num_items = random.randint(1, 5)
        for line_idx in range(1, num_items + 1):
            prod = random.choice(self._master.products)
            qty = float(random.randint(1, 10))
            price = prod.list_price
            disc = round(price * qty * random.choice([0.0, 0.05, 0.10]), 2)
            line_tot = round(qty * price - disc, 2)
            tax = round(line_tot * 0.08, 2)
            subtotal += line_tot

            items.append(
                SalesOrderItem(
                    item_id=f"{order_id}-LINE-{line_idx}",
                    order_id=order_id,
                    product_id=prod.product_id,
                    product_name=prod.product_name,
                    quantity=qty,
                    unit_price=price,
                    discount_amount=disc,
                    tax_amount=tax,
                    total_amount=line_tot + tax,
                )
            )

        tax_total = round(subtotal * 0.08, 2)
        total = subtotal + tax_total

        correlation.add_step("SALES_ORDER", order_id)
        self._event.create_event(EventType.SALES_ORDER_CREATED, "SALES_ORDER", order_id, correlation.correlation_id, order_date)

        return SalesOrder(
            order_id=order_id,
            order_number=f"SO-NUM-{idx:06d}",
            customer_id=customer.customer_id,
            sales_rep_id=sales_rep.employee_id,
            business_unit_id="BU-100",
            order_date=order_date,
            state=TransactionState.APPROVED,
            subtotal=round(subtotal, 2),
            discount_total=0.0,
            tax_total=tax_total,
            total_amount=round(total, 2),
            currency=curr,
            correlation_id=correlation.correlation_id,
            items=items,
            audit_evidence=AuditEvidence(transaction_id=order_id, created_by=sales_rep.employee_id, created_timestamp=order_date, correlation_id=correlation.correlation_id),
        )

    def _generate_shipment(
        self, idx: int, order: SalesOrder, warehouse: Any, ship_date: datetime, correlation: CorrelationContext
    ) -> Shipment:
        shipment_id = f"SHIP-{idx:06d}"
        items = []

        for item in order.items:
            items.append(
                ShipmentItem(
                    shipment_item_id=f"{shipment_id}-LINE-{item.item_id[-1]}",
                    shipment_id=shipment_id,
                    order_item_id=item.item_id,
                    product_id=item.product_id,
                    quantity_shipped=item.quantity,
                )
            )

        correlation.add_step("SHIPMENT", shipment_id)
        self._event.create_event(EventType.SHIPMENT_CREATED, "SHIPMENT", shipment_id, correlation.correlation_id, ship_date)

        return Shipment(
            shipment_id=shipment_id,
            shipment_number=f"SHIP-NUM-{idx:06d}",
            order_id=order.order_id,
            warehouse_id=warehouse.warehouse_id,
            carrier_code="FEDEX_EXPRESS",
            tracking_number=f"TRK-{uuid.uuid4().hex[:12].upper()}",
            shipment_date=ship_date,
            estimated_arrival_date=ship_date + timedelta(days=3),
            state=TransactionState.COMPLETED,
            correlation_id=correlation.correlation_id,
            items=items,
            audit_evidence=AuditEvidence(transaction_id=shipment_id, created_timestamp=ship_date, correlation_id=correlation.correlation_id),
        )

    def _generate_customer_invoice(
        self, idx: int, order: SalesOrder, customer: Any, inv_date: datetime, curr: Currency, correlation: CorrelationContext
    ) -> CustomerInvoice:
        inv_id = f"CUST-INV-{idx:06d}"
        items = []

        for item in order.items:
            items.append(
                CustomerInvoiceItem(
                    invoice_item_id=f"{inv_id}-LINE-{item.item_id[-1]}",
                    invoice_id=inv_id,
                    order_item_id=item.item_id,
                    product_id=item.product_id,
                    quantity_invoiced=item.quantity,
                    unit_price=item.unit_price,
                    line_total=item.total_amount - item.tax_amount,
                )
            )

        correlation.add_step("CUSTOMER_INVOICE", inv_id)
        self._event.create_event(EventType.CUSTOMER_INVOICE_CREATED, "CUSTOMER_INVOICE", inv_id, correlation.correlation_id, inv_date)

        return CustomerInvoice(
            invoice_id=inv_id,
            invoice_number=f"CUST-INV-NUM-{idx:06d}",
            order_id=order.order_id,
            customer_id=customer.customer_id,
            invoice_date=inv_date,
            due_date=inv_date + timedelta(days=30),
            state=TransactionState.SUBMITTED,
            subtotal=order.subtotal,
            tax_amount=order.tax_total,
            total_amount=order.total_amount,
            paid_amount=0.0,
            currency=curr,
            correlation_id=correlation.correlation_id,
            items=items,
            audit_evidence=AuditEvidence(transaction_id=inv_id, created_timestamp=inv_date, correlation_id=correlation.correlation_id),
        )

    def _generate_customer_payment(
        self, idx: int, invoice: CustomerInvoice, customer: Any, pmt_date: datetime, curr: Currency, correlation: CorrelationContext
    ) -> CustomerPayment:
        pmt_id = f"CUST-PMT-{idx:06d}"
        alloc = CustomerPaymentAllocation(
            allocation_id=f"ALLOC-{pmt_id}-{invoice.invoice_id}",
            payment_id=pmt_id,
            invoice_id=invoice.invoice_id,
            allocated_amount=invoice.total_amount,
            allocation_date=pmt_date,
        )

        correlation.add_step("CUSTOMER_PAYMENT", pmt_id)
        self._event.create_event(EventType.CUSTOMER_PAYMENT_RECEIVED, "CUSTOMER_PAYMENT", pmt_id, correlation.correlation_id, pmt_date)

        return CustomerPayment(
            payment_id=pmt_id,
            payment_number=f"CUST-PMT-NUM-{idx:06d}",
            customer_id=customer.customer_id,
            payment_date=pmt_date,
            amount=invoice.total_amount,
            payment_method="WIRE_TRANSFER",
            reference_number=f"REF-{uuid.uuid4().hex[:10].upper()}",
            state=TransactionState.COMPLETED,
            currency=curr,
            correlation_id=correlation.correlation_id,
            allocations=[alloc],
            audit_evidence=AuditEvidence(transaction_id=pmt_id, created_timestamp=pmt_date, correlation_id=correlation.correlation_id),
        )


__all__ = ["O2CProcessResults", "OrderToCashSimulator"]
