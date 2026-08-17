"""Accounting Transaction domain models for Phase 03.3 Enterprise Transaction Simulation Engine.

Covers: Account, Cost Center, Business Unit, Journal Entry, Journal Entry Lines.
Enforces double-entry bookkeeping constraints (Total Debits == Total Credits).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.simulation.domain.enums import Currency
from src.simulation.domain.models.base import AuditEvidence


@dataclass
class Account:
    """General Ledger Account Definition."""

    account_id: str
    account_number: str
    account_name: str
    account_type: str  # ASSET, LIABILITY, EQUITY, REVENUE, EXPENSE
    is_active: bool = True


@dataclass
class CostCenter:
    """Cost Center organizational segment."""

    cost_center_id: str
    cost_center_code: str
    cost_center_name: str
    department_id: str


@dataclass
class BusinessUnit:
    """Business Unit / Legal Entity organizational segment."""

    unit_id: str
    unit_code: str
    unit_name: str
    company_code: str


@dataclass
class JournalEntryLine:
    """Line item in a Journal Entry."""

    line_id: str
    entry_id: str
    line_number: int
    account_id: str
    account_number: str
    account_name: str
    debit: float
    credit: float
    cost_center_id: str | None
    business_unit_id: str | None
    description: str


@dataclass
class JournalEntry:
    """Double-entry balanced accounting Journal Entry."""

    entry_id: str
    entry_number: str
    posting_date: datetime
    accounting_period: str
    source_document_type: str  # P2P_INVOICE, P2P_PAYMENT, O2C_INVOICE, O2C_PAYMENT, PAYROLL, ASSET_DEPR, EXPENSE
    source_document_id: str
    currency: Currency
    exchange_rate: float
    total_debit: float
    total_credit: float
    is_balanced: bool
    correlation_id: str
    lines: list[JournalEntryLine] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)

    def validate_balance(self) -> bool:
        """Verify that total debits equal total credits within rounding tolerance."""
        calculated_debit = sum(round(line.debit, 2) for line in self.lines)
        calculated_credit = sum(round(line.credit, 2) for line in self.lines)
        self.total_debit = calculated_debit
        self.total_credit = calculated_credit
        self.is_balanced = abs(calculated_debit - calculated_credit) < 0.001
        return self.is_balanced


__all__ = [
    "Account",
    "CostCenter",
    "BusinessUnit",
    "JournalEntryLine",
    "JournalEntry",
]
