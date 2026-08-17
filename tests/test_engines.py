"""Unit tests for simulation engines."""

from datetime import datetime
from src.simulation.domain.enums import AnomalyType, Currency, EventType
from src.simulation.domain.models import JournalEntry


def test_temporal_engine(temporal_engine):
    ts = temporal_engine.generate_random_timestamp()
    assert isinstance(ts, datetime)
    assert temporal_engine.get_fiscal_period(ts).startswith(str(ts.year))

    delayed = temporal_engine.add_process_delay(ts, min_hours=2, max_hours=10)
    assert delayed > ts


def test_currency_engine(currency_engine):
    rate = currency_engine.get_exchange_rate(Currency.EUR)
    assert rate > 0.5
    base_amt, r = currency_engine.convert_to_base(100.0, Currency.USD)
    assert base_amt == 100.0
    assert r == 1.0


def test_event_engine(event_engine):
    evt = event_engine.create_event(
        EventType.PURCHASE_ORDER_CREATED, "PURCHASE_ORDER", "PO-100", "CORR-100"
    )
    assert evt.entity_id == "PO-100"
    events = event_engine.get_events()
    assert len(events) == 1
    corr_events = event_engine.get_events_for_correlation("CORR-100")
    assert len(corr_events) == 1


def test_approval_engine(approval_engine):
    req = approval_engine.process_approval_request(
        document_type="SUPPLIER_INVOICE",
        document_id="INV-500",
        requested_by_id="EMP-001",
        amount=15000.0,
        request_date=datetime.now(),
        correlation_id="CORR-500",
    )
    assert len(req.steps) >= 1
    assert req.state.value in ("APPROVED", "REJECTED")


def test_accounting_engine(accounting_engine):
    je = accounting_engine.create_journal_entry(
        source_document_type="P2P_INVOICE",
        source_document_id="INV-100",
        posting_date=datetime.now(),
        currency=Currency.USD,
        exchange_rate=1.0,
        debit_entries=[("120000", 500.0, "Inventory")],
        credit_entries=[("200000", 500.0, "AP")],
        correlation_id="CORR-100",
    )
    assert isinstance(je, JournalEntry)
    assert je.is_balanced is True
    assert je.total_debit == 500.0
    assert je.total_credit == 500.0


def test_anomaly_engine(anomaly_engine):
    rec = anomaly_engine.record_anomaly(
        anomaly_type=AnomalyType.DUPLICATE_INVOICE,
        entity_type="SUPPLIER_INVOICE",
        entity_id="INV-DUP-1",
        description="Duplicate invoice test",
        injection_rule="RULE_DUP",
        expected_detection_rule="DETECT_DUP",
        correlation_id="CORR-ANOM",
    )
    assert rec.entity_id == "INV-DUP-1"
    records = anomaly_engine.get_anomaly_records()
    assert len(records) == 1
