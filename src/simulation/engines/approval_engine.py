"""Approval Workflow Engine for Phase 03.3 Enterprise Transaction Simulation Engine.

Provides multi-tier, configurable approval workflow routing across P2P, O2C, Expense,
Payroll, Asset, and Payment processes.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import random
import uuid

from src.simulation.config.simulation_config import SimulationConfig
from src.simulation.domain.enums import TransactionState
from src.simulation.domain.models.approval import ApprovalRequest, ApprovalStep, Approver
from src.simulation.domain.models.base import AuditEvidence
from src.simulation.master_data.master_data_provider import MasterDataPool


class ApprovalWorkflowEngine:
    """Configurable reusable approval workflow router and evaluator."""

    def __init__(self, config: SimulationConfig, master_pool: MasterDataPool) -> None:
        """Initialize approval engine."""
        self._config = config
        self._master_pool = master_pool
        self._approvers: list[Approver] = []
        self._init_approver_pool()
        random.seed(config.seed)

    def _init_approver_pool(self) -> None:
        """Extract or create approver candidates from employee master pool."""
        managers = [emp for emp in self._master_pool.employees if emp.job_grade in ("G3_SENIOR", "G4_MANAGER", "G5_EXECUTIVE")]
        if not managers:
            managers = self._master_pool.employees

        limits = {
            "G3_SENIOR": 5000.0,
            "G4_MANAGER": 25000.0,
            "G5_EXECUTIVE": 250000.0,
        }

        for emp in managers:
            limit = limits.get(emp.job_grade, 10000.0)
            self._approvers.append(
                Approver(
                    approver_id=f"APPR-{emp.employee_id}",
                    employee_id=emp.employee_id,
                    name=emp.name,
                    job_title=emp.job_title,
                    approval_limit=limit,
                    department_id=emp.department_id,
                )
            )

    def process_approval_request(
        self,
        document_type: str,
        document_id: str,
        requested_by_id: str,
        amount: float,
        request_date: datetime,
        correlation_id: str,
        force_unauthorized: bool = False,
    ) -> ApprovalRequest:
        """Create and evaluate multi-tier approval workflow steps."""
        req_id = f"APPR-REQ-{uuid.uuid4().hex[:10].upper()}"
        request = ApprovalRequest(
            request_id=req_id,
            document_type=document_type,
            document_id=document_id,
            requested_by_id=requested_by_id,
            request_date=request_date,
            amount=amount,
            state=TransactionState.PENDING_APPROVAL,
            correlation_id=correlation_id,
        )

        # Determine required approval tiers based on amount
        num_steps = 1
        if amount > 10000.0:
            num_steps = 2
        if amount > 50000.0:
            num_steps = 3

        current_time = request_date
        all_approved = True

        for step_num in range(1, num_steps + 1):
            eligible = [a for a in self._approvers if a.approval_limit >= (amount / num_steps)]
            if not eligible:
                eligible = self._approvers

            approver = random.choice(eligible)

            # Anomaly injection: Unauthorized approval (approver limit < amount)
            if force_unauthorized:
                low_approvers = [a for a in self._approvers if a.approval_limit < amount]
                if low_approvers:
                    approver = random.choice(low_approvers)

            delay_hours = random.uniform(1.0, 24.0)
            decision_time = current_time + timedelta(hours=delay_hours)
            current_time = decision_time

            # High probability of approval (95%), 5% rejection
            decision = "APPROVED"
            if not force_unauthorized and random.random() < 0.05:
                decision = "REJECTED"
                all_approved = False

            step = ApprovalStep(
                step_id=f"STEP-{uuid.uuid4().hex[:8].upper()}",
                request_id=req_id,
                step_number=step_num,
                approver_id=approver.approver_id,
                approver_name=approver.name,
                decision=decision,
                decision_timestamp=decision_time,
                comments=f"Step {step_num} evaluation: {decision}",
                sla_hours=24.0,
                sla_breached=(delay_hours > 24.0),
            )
            request.steps.append(step)

            if not all_approved:
                break

        if all_approved:
            request.state = TransactionState.APPROVED
            request.audit_evidence = AuditEvidence(
                transaction_id=req_id,
                approved_by=request.steps[-1].approver_id if request.steps else "AUTO_APPROVER",
                approval_timestamp=current_time,
                correlation_id=correlation_id,
                source_document_id=document_id,
            )
        else:
            request.state = TransactionState.REJECTED

        return request


__all__ = ["ApprovalWorkflowEngine"]
