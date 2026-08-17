"""Expense-to-Reimbursement Process Simulator for Phase 03.3 Enterprise Transaction Simulation Engine.

Orchestrates Expense Claim submission, receipt verification, manager/finance approvals,
reimbursements, reversals, accounting postings, and anomaly injection (unusual expenses, policy violations).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import random

from src.simulation.config.process_config import ExpenseProcessConfig
from src.simulation.config.simulation_config import SimulationConfig
from src.simulation.domain.enums import AnomalySeverity, AnomalyType, Currency, EventType, TransactionState
from src.simulation.domain.models import (
    AuditEvidence,
    CorrelationContext,
    ExpenseCategory,
    ExpenseClaim,
    ExpenseClaimItem,
    ExpenseReimbursement,
    ExpenseReversal,
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
class ExpenseProcessResults:
    """Container for all expense simulation outputs."""

    categories: list[ExpenseCategory] = field(default_factory=list)
    claims: list[ExpenseClaim] = field(default_factory=list)
    reimbursements: list[ExpenseReimbursement] = field(default_factory=list)
    reversals: list[ExpenseReversal] = field(default_factory=list)


class ExpenseToReimbursementSimulator:
    """End-to-end Expense-to-Reimbursement process simulator."""

    def __init__(
        self,
        config: SimulationConfig,
        exp_config: ExpenseProcessConfig,
        master_pool: MasterDataPool,
        temporal_engine: TemporalEngine,
        currency_engine: CurrencyEngine,
        event_engine: BusinessEventEngine,
        approval_engine: ApprovalWorkflowEngine,
        accounting_engine: AccountingTransactionEngine,
        anomaly_engine: AnomalyEngine,
    ) -> None:
        """Initialize Expense simulator."""
        self._config = config
        self._exp_config = exp_config
        self._master = master_pool
        self._temporal = temporal_engine
        self._currency = currency_engine
        self._event = event_engine
        self._approval = approval_engine
        self._accounting = accounting_engine
        self._anomaly = anomaly_engine
        self._categories: list[ExpenseCategory] = []
        self._init_categories()
        random.seed(config.seed)

    def _init_categories(self) -> None:
        """Initialize expense category taxonomy."""
        self._categories = [
            ExpenseCategory("CAT-TRAVEL", "Airfare & Travel", requires_receipt_threshold=25.0, is_per_diem_eligible=False, policy_limit=2500.0),
            ExpenseCategory("CAT-MEALS", "Client Meals & Entertainment", requires_receipt_threshold=25.0, is_per_diem_eligible=True, policy_limit=250.0),
            ExpenseCategory("CAT-HOTEL", "Lodging & Accommodation", requires_receipt_threshold=25.0, is_per_diem_eligible=False, policy_limit=500.0),
            ExpenseCategory("CAT-SUPPLIES", "Office Supplies", requires_receipt_threshold=50.0, is_per_diem_eligible=False, policy_limit=1000.0),
        ]

    def simulate(self, target_claims: int) -> ExpenseProcessResults:
        """Execute Expense simulation producing target claim count."""
        results = ExpenseProcessResults()
        results.categories = list(self._categories)
        logger.info("Executing Expense-to-Reimbursement Simulation (Target Claims: %d)...", target_claims)

        for i in range(1, target_claims + 1):
            correlation = CorrelationContext(process_type="EXPENSE")
            employee = random.choice(self._master.employees)
            sub_date = self._temporal.generate_random_timestamp()
            curr = random.choice(self._config.enabled_currencies)
            ex_rate = self._currency.get_exchange_rate(curr)

            # Generate Expense Claim
            claim = self._generate_claim(i, employee, sub_date, curr, correlation)
            results.claims.append(claim)

            # Check Anomaly Injection: Unusual Expense / Policy Violation
            if self._anomaly.should_inject("EXPENSE") and random.random() < 0.15:
                # Excessive weekend meal claim
                self._anomaly.record_anomaly(
                    anomaly_type=AnomalyType.UNUSUAL_EXPENSE,
                    entity_type="EXPENSE_CLAIM",
                    entity_id=claim.claim_id,
                    description=f"Unusual weekend expense claim of ${claim.total_amount:.2f} submitted by {employee.name}",
                    injection_rule="WEEKEND_HIGH_AMOUNT_EXPENSE",
                    expected_detection_rule="DETECT_OUTLIER_EXPENSE_CLAIMS",
                    correlation_id=correlation.correlation_id,
                )

            # Approval
            appr_req = self._approval.process_approval_request(
                document_type="EXPENSE_CLAIM",
                document_id=claim.claim_id,
                requested_by_id=employee.employee_id,
                amount=claim.total_amount,
                request_date=sub_date,
                correlation_id=correlation.correlation_id,
            )

            if appr_req.state == TransactionState.APPROVED:
                claim.state = TransactionState.APPROVED
                claim.approved_amount = claim.total_amount
                self._event.create_event(EventType.EXPENSE_APPROVED, "EXPENSE_CLAIM", claim.claim_id, correlation.correlation_id, sub_date)

                # Post Expense Journal Entry (Debit Travel/Expense 610000, Credit AP/Employee Payable 200000)
                self._accounting.create_journal_entry(
                    source_document_type="EXPENSE",
                    source_document_id=claim.claim_id,
                    posting_date=sub_date,
                    currency=curr,
                    exchange_rate=ex_rate,
                    debit_entries=[("610000", claim.total_amount, "Employee Expense Claim")],
                    credit_entries=[("200000", claim.total_amount, "Employee Payable")],
                    correlation_id=correlation.correlation_id,
                    cost_center_id=employee.cost_center_id,
                )

                # Reimbursement Disbursal
                reimb_date = self._temporal.add_process_delay(sub_date, min_hours=24, max_hours=120)
                reimb = ExpenseReimbursement(
                    reimbursement_id=f"REIMB-{i:06d}",
                    reimbursement_number=f"REIMB-NUM-{i:06d}",
                    claim_id=claim.claim_id,
                    employee_id=employee.employee_id,
                    payment_date=reimb_date,
                    amount=claim.approved_amount,
                    payment_method="DIRECT_DEPOSIT",
                    bank_account_id=f"BANK-EMP-{employee.employee_id}",
                    state=TransactionState.COMPLETED,
                    correlation_id=correlation.correlation_id,
                    audit_evidence=AuditEvidence(transaction_id=f"REIMB-{i:06d}", created_timestamp=reimb_date, correlation_id=correlation.correlation_id),
                )
                results.reimbursements.append(reimb)
                claim.state = TransactionState.COMPLETED
                self._event.create_event(EventType.REIMBURSEMENT_ISSUED, "EXPENSE_REIMBURSEMENT", reimb.reimbursement_id, correlation.correlation_id, reimb_date)

        logger.info("Expense-to-Reimbursement Simulation Complete: %d Claims, %d Reimbursements.", len(results.claims), len(results.reimbursements))
        return results

    def _generate_claim(
        self, idx: int, employee: Any, sub_date: datetime, curr: Currency, correlation: CorrelationContext
    ) -> ExpenseClaim:
        claim_id = f"EXP-{idx:06d}"
        items = []
        tot = 0.0

        num_items = random.randint(1, 3)
        for line_idx in range(1, num_items + 1):
            cat = random.choice(self._categories)
            amt = round(random.uniform(20.0, cat.policy_limit * 0.8), 2)
            tot += amt

            items.append(
                ExpenseClaimItem(
                    item_id=f"{claim_id}-LINE-{line_idx}",
                    claim_id=claim_id,
                    category_id=cat.category_id,
                    category_name=cat.category_name,
                    expense_date=sub_date - timedelta(days=random.randint(1, 5)),
                    merchant_name=f"Merchant {cat.category_name[:6]}",
                    amount=amt,
                    tax_amount=round(amt * 0.05, 2),
                    has_receipt_attached=(amt >= cat.requires_receipt_threshold),
                    description=f"Business expense for {cat.category_name}",
                )
            )

        correlation.add_step("EXPENSE_CLAIM", claim_id)
        self._event.create_event(EventType.EXPENSE_SUBMITTED, "EXPENSE_CLAIM", claim_id, correlation.correlation_id, sub_date)

        return ExpenseClaim(
            claim_id=claim_id,
            claim_number=f"EXP-NUM-{idx:06d}",
            employee_id=employee.employee_id,
            department_id=employee.department_id,
            cost_center_id=employee.cost_center_id,
            submission_date=sub_date,
            purpose="Client Travel & Business Operations",
            total_amount=round(tot, 2),
            approved_amount=0.0,
            currency=curr,
            state=TransactionState.PENDING_APPROVAL,
            correlation_id=correlation.correlation_id,
            items=items,
            audit_evidence=AuditEvidence(transaction_id=claim_id, created_by=employee.employee_id, created_timestamp=sub_date, correlation_id=correlation.correlation_id),
        )


__all__ = ["ExpenseProcessResults", "ExpenseToReimbursementSimulator"]
