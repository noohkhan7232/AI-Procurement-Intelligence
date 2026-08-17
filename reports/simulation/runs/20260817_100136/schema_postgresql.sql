-- EAAP Enterprise Transaction Engine PostgreSQL Schema DDL
-- Generated for Phase 03.3 Simulation

CREATE SCHEMA IF NOT EXISTS eaap_dw;

-- P2P Tables
CREATE TABLE IF NOT EXISTS eaap_dw.purchase_requisitions (
    requisition_id VARCHAR(64) PRIMARY KEY,
    requisition_number VARCHAR(64) NOT NULL,
    requester_id VARCHAR(64) NOT NULL,
    department_id VARCHAR(64) NOT NULL,
    business_unit_id VARCHAR(64) NOT NULL,
    creation_date TIMESTAMP NOT NULL,
    state VARCHAR(32) NOT NULL,
    total_amount NUMERIC(18,2) NOT NULL,
    currency VARCHAR(8) NOT NULL,
    correlation_id VARCHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS eaap_dw.purchase_orders (
    po_id VARCHAR(64) PRIMARY KEY,
    po_number VARCHAR(64) NOT NULL,
    requisition_id VARCHAR(64),
    supplier_id VARCHAR(64) NOT NULL,
    buyer_id VARCHAR(64) NOT NULL,
    company_code VARCHAR(16) NOT NULL,
    business_unit_id VARCHAR(64) NOT NULL,
    po_date TIMESTAMP NOT NULL,
    state VARCHAR(32) NOT NULL,
    total_amount NUMERIC(18,2) NOT NULL,
    currency VARCHAR(8) NOT NULL,
    exchange_rate NUMERIC(12,4) NOT NULL,
    correlation_id VARCHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS eaap_dw.goods_receipts (
    receipt_id VARCHAR(64) PRIMARY KEY,
    receipt_number VARCHAR(64) NOT NULL,
    po_id VARCHAR(64) REFERENCES eaap_dw.purchase_orders(po_id),
    supplier_id VARCHAR(64) NOT NULL,
    warehouse_id VARCHAR(64) NOT NULL,
    received_by_id VARCHAR(64) NOT NULL,
    receipt_date TIMESTAMP NOT NULL,
    state VARCHAR(32) NOT NULL,
    correlation_id VARCHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS eaap_dw.supplier_invoices (
    invoice_id VARCHAR(64) PRIMARY KEY,
    invoice_number VARCHAR(64) NOT NULL,
    supplier_id VARCHAR(64) NOT NULL,
    po_id VARCHAR(64) REFERENCES eaap_dw.purchase_orders(po_id),
    receipt_id VARCHAR(64) REFERENCES eaap_dw.goods_receipts(receipt_id),
    invoice_date TIMESTAMP NOT NULL,
    due_date TIMESTAMP NOT NULL,
    state VARCHAR(32) NOT NULL,
    subtotal NUMERIC(18,2) NOT NULL,
    tax_amount NUMERIC(18,2) NOT NULL,
    total_amount NUMERIC(18,2) NOT NULL,
    paid_amount NUMERIC(18,2) NOT NULL,
    currency VARCHAR(8) NOT NULL,
    correlation_id VARCHAR(64) NOT NULL
);

CREATE TABLE IF NOT EXISTS eaap_dw.payments (
    payment_id VARCHAR(64) PRIMARY KEY,
    payment_number VARCHAR(64) NOT NULL,
    supplier_id VARCHAR(64) NOT NULL,
    payment_date TIMESTAMP NOT NULL,
    amount NUMERIC(18,2) NOT NULL,
    payment_method VARCHAR(32) NOT NULL,
    bank_account_id VARCHAR(64) NOT NULL,
    state VARCHAR(32) NOT NULL,
    currency VARCHAR(8) NOT NULL,
    correlation_id VARCHAR(64) NOT NULL
);

-- Accounting Journal Entries
CREATE TABLE IF NOT EXISTS eaap_dw.journal_entries (
    entry_id VARCHAR(64) PRIMARY KEY,
    entry_number VARCHAR(64) NOT NULL,
    posting_date TIMESTAMP NOT NULL,
    accounting_period VARCHAR(16) NOT NULL,
    source_document_type VARCHAR(64) NOT NULL,
    source_document_id VARCHAR(64) NOT NULL,
    currency VARCHAR(8) NOT NULL,
    exchange_rate NUMERIC(12,4) NOT NULL,
    total_debit NUMERIC(18,2) NOT NULL,
    total_credit NUMERIC(18,2) NOT NULL,
    is_balanced BOOLEAN NOT NULL,
    correlation_id VARCHAR(64) NOT NULL
);

-- Audit Anomaly Metadata
CREATE TABLE IF NOT EXISTS eaap_dw.anomaly_metadata (
    anomaly_id VARCHAR(64) PRIMARY KEY,
    anomaly_type VARCHAR(64) NOT NULL,
    severity VARCHAR(16) NOT NULL,
    entity_type VARCHAR(64) NOT NULL,
    entity_id VARCHAR(64) NOT NULL,
    injection_timestamp TIMESTAMP NOT NULL,
    injection_rule TEXT NOT NULL,
    expected_detection_rule TEXT NOT NULL,
    description TEXT NOT NULL,
    correlation_id VARCHAR(64) NOT NULL
);
