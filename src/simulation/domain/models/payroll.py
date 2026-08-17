"""Hire-to-Payroll domain models for Phase 03.3 Enterprise Transaction Simulation Engine.

Covers: Payroll Period, Employee Payroll Record, Salary Components, Overtime,
Bonus, Deduction, Tax Withholding, Payroll Approval, Disbursal, Adjustment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.simulation.domain.enums import Currency, TransactionState
from src.simulation.domain.models.base import AuditEvidence


@dataclass
class SalaryComponent:
    """Breakdown component of an employee salary."""

    component_id: str
    component_name: str  # BASIC, HOUSING, TRANSPORT, OVERTIME, BONUS, TAX, PENSION
    component_type: str  # EARNING, DEDUCTION
    amount: float


@dataclass
class PayrollPeriod:
    """Payroll Period calendar master."""

    period_id: str
    period_name: str
    start_date: datetime
    end_date: datetime
    payment_date: datetime
    state: TransactionState


@dataclass
class EmployeePayrollRecord:
    """Per-employee payroll calculation for a period."""

    record_id: str
    period_id: str
    employee_id: str
    employee_name: str
    department_id: str
    cost_center_id: str
    job_grade: str
    base_salary: float
    gross_earnings: float
    total_deductions: float
    net_pay: float
    tax_withheld: float
    overtime_hours: float
    overtime_pay: float
    currency: Currency
    state: TransactionState
    correlation_id: str
    components: list[SalaryComponent] = field(default_factory=list)
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class PayrollPayment:
    """Disbursal payment of payroll batch."""

    payment_id: str
    period_id: str
    payment_date: datetime
    total_disbursed_amount: float
    employee_count: int
    bank_batch_reference: str
    state: TransactionState
    correlation_id: str
    audit_evidence: AuditEvidence = field(default_factory=AuditEvidence)


@dataclass
class PayrollAdjustment:
    """Retroactive payroll adjustment."""

    adjustment_id: str
    employee_id: str
    original_period_id: str
    adjustment_amount: float
    reason: str
    processed_date: datetime
    correlation_id: str


__all__ = [
    "SalaryComponent",
    "PayrollPeriod",
    "EmployeePayrollRecord",
    "PayrollPayment",
    "PayrollAdjustment",
]
