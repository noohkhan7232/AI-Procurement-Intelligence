"""Approval Workflow domain models for Phase 03.3 Enterprise Transaction Simulation Engine.

Covers: Approval Policy, Request, Steps, Approver, Decisions, Delegation, SLA metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.simulation.domain.enums import TransactionState
from src.simulation.domain.models.base import AuditEvidence


@dataclass
class Approver:
    """An individual authorized to grant approval."""

    approver_id: str
    employee_id: str
    name: str
    job_title: str
    approval_limit: float
    department_id: str


@dataclass
class ApprovalStep:
    """A step within a multi-tier approval workflow."""

    step_id: str
    request_id: str
    step_number: int
    approver_id: str
    approver_name: str
    decision: str  # APPROVED, REJECTED, ESCALATED, DELEGATED, PENDING
    decision_timestamp: datetime | None
    comments: str | None
    sla_hours: float
    sla_breached: bool = False


@dataclass
class ApprovalRequest:
    """An approval request container across any operational document type."""

    request_id: str
    document_type: str  # PURCHASE_ORDER, INVOICE, EXPENSE_CLAIM, PAYROLL, ASSET_DISPOSAL, PAYMENT
    document_id: str
    requested_by_id: str
    request_date: datetime
    amount: float
    state: TransactionState
    correlation_id: str
    steps: list[ApprovalStep] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class DelegationRecord:
    """Approval authority delegation record."""

    delegation_id: str
    from_approver_id: str
    to_approver_id: str
    start_date: datetime
    end_date: datetime
    reason: str


__all__ = [
    "Approver",
    "ApprovalStep",
    "ApprovalRequest",
    "DelegationRecord",
]
