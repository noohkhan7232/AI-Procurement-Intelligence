"""Unit tests for simulation domain models."""

from datetime import datetime
from src.simulation.domain.enums import Currency, EventType, EvidenceStatus, TransactionState
from src.simulation.domain.models import AuditEvidence, BusinessEvent, CorrelationContext, JournalEntry, JournalEntryLine


def test_correlation_context():
    ctx = CorrelationContext(process_type="P2P")
    assert ctx.correlation_id.startswith("CORR-")
    ctx.add_step("PO", "PO-001")
    assert "PO:PO-001" in ctx.trace_steps


def test_audit_evidence_to_dict():
    evid = AuditEvidence(transaction_id="TX-100", correlation_id="CORR-100")
    d = evid.to_dict()
    assert d["transaction_id"] == "TX-100"
    assert d["correlation_id"] == "CORR-100"
    assert d["evidence_status"] == EvidenceStatus.GENERATED.value


def test_business_event_to_dict():
    evt = BusinessEvent(
        event_type=EventType.PURCHASE_ORDER_CREATED,
        entity_type="PURCHASE_ORDER",
        entity_id="PO-001",
        correlation_id="CORR-100",
    )
    d = evt.to_dict()
    assert d["event_type"] == EventType.PURCHASE_ORDER_CREATED.value
    assert d["entity_id"] == "PO-001"


def test_journal_entry_balance():
    je = JournalEntry(
        entry_id="JE-1",
        entry_number="JE-NUM-1",
        posting_date=datetime.now(),
        accounting_period="2025-01",
        source_document_type="P2P_INVOICE",
        source_document_id="INV-1",
        currency=Currency.USD,
        exchange_rate=1.0,
        total_debit=0.0,
        total_credit=0.0,
        is_balanced=False,
        correlation_id="CORR-1",
        lines=[
            JournalEntryLine("L1", "JE-1", 1, "ACC-1", "120000", "Inventory", 100.0, 0.0, None, None, "Debit"),
            JournalEntryLine("L2", "JE-1", 2, "ACC-2", "200000", "AP", 0.0, 100.0, None, None, "Credit"),
        ],
    )
    assert je.validate_balance() is True
    assert je.is_balanced is True
