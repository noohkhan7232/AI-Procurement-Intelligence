"""Hire-to-Payroll Process Simulator for Phase 03.3 Enterprise Transaction Simulation Engine.

Orchestrates Payroll Period opening, employee payroll record calculation (Base, Overtime, Bonus,
Deductions, Tax Withholding), Approval, Disbursal, Accounting entries, and Payroll Anomaly Injection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import random

from src.simulation.config.process_config import PayrollProcessConfig
from src.simulation.config.simulation_config import SimulationConfig
from src.simulation.domain.enums import AnomalySeverity, AnomalyType, Currency, EventType, TransactionState
from src.simulation.domain.models import (
    AuditEvidence,
    CorrelationContext,
    EmployeePayrollRecord,
    PayrollAdjustment,
    PayrollPayment,
    PayrollPeriod,
    SalaryComponent,
)
from src.simulation.engines import (
    AccountingTransactionEngine,
    AnomalyEngine,
    ApprovalWorkflowEngine,
    BusinessEventEngine,
    TemporalEngine,
)
from src.simulation.master_data import MasterDataPool
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PayrollProcessResults:
    """Container for all payroll simulation outputs."""

    periods: list[PayrollPeriod] = field(default_factory=list)
    records: list[EmployeePayrollRecord] = field(default_factory=list)
    payments: list[PayrollPayment] = field(default_factory=list)
    adjustments: list[PayrollAdjustment] = field(default_factory=list)


class HireToPayrollSimulator:
    """End-to-end Hire-to-Payroll process simulator."""

    def __init__(
        self,
        config: SimulationConfig,
        pay_config: PayrollProcessConfig,
        master_pool: MasterDataPool,
        temporal_engine: TemporalEngine,
        event_engine: BusinessEventEngine,
        approval_engine: ApprovalWorkflowEngine,
        accounting_engine: AccountingTransactionEngine,
        anomaly_engine: AnomalyEngine,
    ) -> None:
        """Initialize Payroll simulator."""
        self._config = config
        self._pay_config = pay_config
        self._master = master_pool
        self._temporal = temporal_engine
        self._event = event_engine
        self._approval = approval_engine
        self._accounting = accounting_engine
        self._anomaly = anomaly_engine
        random.seed(config.seed)

    def simulate(self, target_periods: int) -> PayrollProcessResults:
        """Execute Payroll simulation generating target payroll periods."""
        results = PayrollProcessResults()
        logger.info("Executing Hire-to-Payroll Simulation (Target Periods: %d)...", target_periods)

        start_dt = self._config.start_date

        for p_idx in range(1, target_periods + 1):
            correlation = CorrelationContext(process_type="PAYROLL")

            # Determine period start and end dates (Monthly cycle)
            p_start = start_dt + timedelta(days=(p_idx - 1) * 30)
            p_end = p_start + timedelta(days=29)
            pmt_date = p_end + timedelta(days=2)

            period = PayrollPeriod(
                period_id=f"PER-{p_idx:03d}",
                period_name=p_start.strftime("%Y-%B"),
                start_date=p_start,
                end_date=p_end,
                payment_date=pmt_date,
                state=TransactionState.COMPLETED,
            )
            results.periods.append(period)
            self._event.create_event(EventType.PAYROLL_PERIOD_OPENED, "PAYROLL_PERIOD", period.period_id, correlation.correlation_id, p_start)

            total_period_disbursed = 0.0

            # Calculate payroll for active employee pool
            for emp in self._master.employees:
                if not emp.is_active:
                    continue

                rec = self._calculate_employee_payroll(emp, period, pmt_date, correlation)
                results.records.append(rec)
                total_period_disbursed += rec.net_pay

                # Anomaly injection: Ghost Employee / Payroll Anomaly
                if self._anomaly.should_inject("PAYROLL") and random.random() < 0.02:
                    self._anomaly.record_anomaly(
                        anomaly_type=AnomalyType.GHOST_EMPLOYEE_PATTERN,
                        entity_type="PAYROLL_RECORD",
                        entity_id=rec.record_id,
                        description=f"Payroll record generated for terminated/ghost employee {emp.name}",
                        injection_rule="INJECT_GHOST_EMPLOYEE_PAYROLL",
                        expected_detection_rule="DETECT_GHOST_EMPLOYEE_PAYMENTS",
                        correlation_id=correlation.correlation_id,
                    )

            # Approval for total batch
            appr = self._approval.process_approval_request(
                document_type="PAYROLL_BATCH",
                document_id=period.period_id,
                requested_by_id="HR-DIRECTOR-01",
                amount=total_period_disbursed,
                request_date=p_end,
                correlation_id=correlation.correlation_id,
            )

            if appr.state == TransactionState.APPROVED:
                self._event.create_event(EventType.PAYROLL_APPROVED, "PAYROLL_PERIOD", period.period_id, correlation.correlation_id, p_end)

                # Payroll Disbursal Payment
                pay_disb = PayrollPayment(
                    payment_id=f"PAY-DISB-{p_idx:03d}",
                    period_id=period.period_id,
                    payment_date=pmt_date,
                    total_disbursed_amount=round(total_period_disbursed, 2),
                    employee_count=len([e for e in self._master.employees if e.is_active]),
                    bank_batch_reference=f"BANK-PAY-BATCH-{p_idx:03d}",
                    state=TransactionState.COMPLETED,
                    correlation_id=correlation.correlation_id,
                    audit_evidence=AuditEvidence(transaction_id=f"PAY-DISB-{p_idx:03d}", created_timestamp=pmt_date, correlation_id=correlation.correlation_id),
                )
                results.payments.append(pay_disb)

                # Post Payroll Accounting Entries (Debit Salary Expense 600000, Credit Payroll Payable 210000 & Tax Payable 220000)
                self._accounting.create_journal_entry(
                    source_document_type="PAYROLL",
                    source_document_id=period.period_id,
                    posting_date=pmt_date,
                    currency=Currency.USD,
                    exchange_rate=1.0,
                    debit_entries=[("600000", total_period_disbursed, "Monthly Payroll Expense")],
                    credit_entries=[("100000", total_period_disbursed, "Payroll Cash Disbursal")],
                    correlation_id=correlation.correlation_id,
                )

        logger.info("Hire-to-Payroll Simulation Complete: %d Periods, %d Employee Records.", len(results.periods), len(results.records))
        return results

    def _calculate_employee_payroll(
        self, emp: Any, period: PayrollPeriod, pmt_date: datetime, correlation: CorrelationContext
    ) -> EmployeePayrollRecord:
        monthly_base = round(emp.base_salary / 12.0, 2)
        ot_hours = round(random.choice([0.0, 0.0, 5.0, 10.0, 12.5]), 1)
        ot_pay = round(ot_hours * (monthly_base / 160.0) * self._pay_config.overtime_multiplier, 2)
        gross = monthly_base + ot_pay

        tax = round(gross * self._pay_config.tax_withholding_rate_avg, 2)
        pension = round(gross * self._pay_config.pension_contribution_rate, 2)
        total_ded = tax + pension
        net = round(gross - total_ded, 2)

        comps = [
            SalaryComponent(f"SC-BASE-{emp.employee_id}", "Basic Monthly Salary", "EARNING", monthly_base),
            SalaryComponent(f"SC-OT-{emp.employee_id}", "Overtime Pay", "EARNING", ot_pay),
            SalaryComponent(f"SC-TAX-{emp.employee_id}", "Income Tax Withholding", "DEDUCTION", tax),
            SalaryComponent(f"SC-PEN-{emp.employee_id}", "Pension Contribution", "DEDUCTION", pension),
        ]

        rec_id = f"PAYREC-{period.period_id}-{emp.employee_id}"
        return EmployeePayrollRecord(
            record_id=rec_id,
            period_id=period.period_id,
            employee_id=emp.employee_id,
            employee_name=emp.name,
            department_id=emp.department_id,
            cost_center_id=emp.cost_center_id,
            job_grade=emp.job_grade,
            base_salary=emp.base_salary,
            gross_earnings=gross,
            total_deductions=total_ded,
            net_pay=net,
            tax_withheld=tax,
            overtime_hours=ot_hours,
            overtime_pay=ot_pay,
            currency=Currency.USD,
            state=TransactionState.COMPLETED,
            correlation_id=correlation.correlation_id,
            components=comps,
            audit_evidence=AuditEvidence(transaction_id=rec_id, created_timestamp=pmt_date, correlation_id=correlation.correlation_id),
        )


__all__ = ["PayrollProcessResults", "HireToPayrollSimulator"]
