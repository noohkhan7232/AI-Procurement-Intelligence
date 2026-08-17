"""Expense-to-Reimbursement domain models for Phase 03.3 Enterprise Transaction Simulation Engine.

Covers: Expense Category, Expense Claim, Claim Items, Receipt Evidence, Approval,
Reimbursement, Expense Reversal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.simulation.domain.enums import Currency, TransactionState
from src.simulation.domain.models.base import AuditEvidence


@dataclass
class ExpenseCategory:
    """Category classification for employee expenses."""

    category_id: str
    category_name: str
    requires_receipt_threshold: float
    is_per_diem_eligible: bool
    policy_limit: float


@dataclass
class ExpenseClaimItem:
    """Line item in an Expense Claim."""

    item_id: str
    claim_id: str
    category_id: str
    category_name: str
    expense_date: datetime
    merchant_name: str
    amount: float
    tax_amount: float
    has_receipt_attached: bool
    description: str


@dataclass
class ExpenseClaim:
    """Expense Claim header."""

    claim_id: str
    claim_number: str
    employee_id: str
    department_id: str
    cost_center_id: str
    submission_date: datetime
    purpose: str
    total_amount: float
    approved_amount: float
    currency: Currency
    state: TransactionState
    correlation_id: str
    items: list[ExpenseClaimItem] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class ExpenseReimbursement:
    """Expense Reimbursement disbursement."""

    reimbursement_id: str
    reimbursement_number: str
    claim_id: str
    employee_id: str
    payment_date: datetime
    amount: float
    payment_method: str
    bank_account_id: str
    state: TransactionState
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class ExpenseReversal:
    """Expense Reversal for rejected/overpaid claim."""

    reversal_id: str
    claim_id: str
    employee_id: str
    reversal_date: datetime
    reversal_amount: float
    reason: str
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


__all__ = [
    "ExpenseCategory",
    "ExpenseClaimItem",
    "ExpenseClaim",
    "ExpenseReimbursement",
    "ExpenseReversal",
]
