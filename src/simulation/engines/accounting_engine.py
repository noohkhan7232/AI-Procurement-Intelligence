"""Accounting Transaction Engine for Phase 03.3 Enterprise Transaction Simulation Engine.

Generates double-entry balanced JournalEntry and JournalEntryLine records for all business
events across P2P, O2C, Inventory, Expense, Payroll, and Asset domains.
"""

from __future__ import annotations

from datetime import datetime
import uuid

from src.simulation.domain.enums import Currency
from src.simulation.domain.models.accounting import JournalEntry, JournalEntryLine
from src.simulation.domain.models.base import AuditEvidence
from src.simulation.master_data.master_data_provider import MasterDataPool


class AccountingTransactionEngine:
    """Generates double-entry balanced general ledger transactions."""

    def __init__(self, master_pool: MasterDataPool) -> None:
        """Initialize accounting engine with master accounts pool."""
        self._master_pool = master_pool
        self._accounts_by_number = {acc["account_number"]: acc for acc in master_pool.accounts}

    def create_journal_entry(
        self,
        source_document_type: str,
        source_document_id: str,
        posting_date: datetime,
        currency: Currency,
        exchange_rate: float,
        debit_entries: list[tuple[str, float, str]],  # (account_number, amount, description)
        credit_entries: list[tuple[str, float, str]],  # (account_number, amount, description)
        correlation_id: str,
        cost_center_id: str | None = None,
        business_unit_id: str | None = None,
    ) -> JournalEntry:
        """Create a balanced double-entry journal entry."""
        entry_id = f"JE-{uuid.uuid4().hex[:10].upper()}"
        entry_number = f"JE-NUM-{uuid.uuid4().hex[:8].upper()}"

        je = JournalEntry(
            entry_id=entry_id,
            entry_number=entry_number,
            posting_date=posting_date,
            accounting_period=posting_date.strftime("%Y-%m"),
            source_document_type=source_document_type,
            source_document_id=source_document_id,
            currency=currency,
            exchange_rate=exchange_rate,
            total_debit=0.0,
            total_credit=0.0,
            is_balanced=False,
            correlation_id=correlation_id,
        )

        line_num = 1
        for acc_num, amt, desc in debit_entries:
            acc_info = self._accounts_by_number.get(acc_num, {"account_id": f"ACC-{acc_num}", "account_name": "General Account"})
            line = JournalEntryLine(
                line_id=f"{entry_id}-L{line_num}",
                entry_id=entry_id,
                line_number=line_num,
                account_id=acc_info["account_id"],
                account_number=acc_num,
                account_name=acc_info["account_name"],
                debit=round(amt, 2),
                credit=0.0,
                cost_center_id=cost_center_id,
                business_unit_id=business_unit_id,
                description=desc,
            )
            je.lines.append(line)
            line_num += 1

        for acc_num, amt, desc in credit_entries:
            acc_info = self._accounts_by_number.get(acc_num, {"account_id": f"ACC-{acc_num}", "account_name": "General Account"})
            line = JournalEntryLine(
                line_id=f"{entry_id}-L{line_num}",
                entry_id=entry_id,
                line_number=line_num,
                account_id=acc_info["account_id"],
                account_number=acc_num,
                account_name=acc_info["account_name"],
                debit=0.0,
                credit=round(amt, 2),
                cost_center_id=cost_center_id,
                business_unit_id=business_unit_id,
                description=desc,
            )
            je.lines.append(line)
            line_num += 1

        je.validate_balance()
        je.audit_evidence = AuditEvidence(
            transaction_id=entry_id,
            correlation_id=correlation_id,
            source_document_id=source_document_id,
        )
        return je


__all__ = ["AccountingTransactionEngine"]
