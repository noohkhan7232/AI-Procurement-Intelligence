"""Base domain abstractions for Phase 03.3 Enterprise Transaction Simulation Engine.

Provides core abstractions for audit evidence, business events, and correlation context
used across all process simulation modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
import uuid

from src.simulation.domain.enums import EventType, EvidenceStatus


@dataclass
class CorrelationContext:
    """End-to-end correlation container linking all events in a business process lifecycle."""

    correlation_id: str = field(default_factory=lambda: f"CORR-{uuid.uuid4().hex[:12].upper()}")
    process_type: str = "GENERIC"
    parent_correlation_id: str | None = None
    trace_steps: list[str] = field(default_factory=list)

    def add_step(self, step_name: str, entity_id: str) -> None:
        """Record a step in the business process trace."""
        self.trace_steps.append(f"{step_name}:{entity_id}")


@dataclass
class AuditEvidence:
    """Immutable audit trail metadata attached to every operational entity."""

    evidence_id: str = field(default_factory=lambda: f"EVID-{uuid.uuid4().hex[:10].upper()}")
    transaction_id: str = ""
    source_system: str = "EAAP-ERP-SIMULATOR"
    created_by: str = "SYSTEM_USER"
    created_timestamp: datetime = field(default_factory=datetime.now)
    approved_by: str | None = None
    approval_timestamp: datetime | None = None
    modified_by: str | None = None
    modified_timestamp: datetime | None = None
    business_event_id: str | None = None
    correlation_id: str = ""
    source_document_id: str | None = None
    evidence_type: str = "TRANSACTION_LOG"
    evidence_status: EvidenceStatus = EvidenceStatus.GENERATED
    hash_checksum: str = field(default_factory=lambda: uuid.uuid4().hex)

    def to_dict(self) -> dict[str, Any]:
        """Convert audit evidence instance to dictionary representation."""
        return {
            "evidence_id": self.evidence_id,
            "transaction_id": self.transaction_id,
            "source_system": self.source_system,
            "created_by": self.created_by,
            "created_timestamp": self.created_timestamp.isoformat(),
            "approved_by": self.approved_by,
            "approval_timestamp": self.approval_timestamp.isoformat() if self.approval_timestamp else None,
            "modified_by": self.modified_by,
            "modified_timestamp": self.modified_timestamp.isoformat() if self.modified_timestamp else None,
            "business_event_id": self.business_event_id,
            "correlation_id": self.correlation_id,
            "source_document_id": self.source_document_id,
            "evidence_type": self.evidence_type,
            "evidence_status": self.evidence_status.value,
            "hash_checksum": self.hash_checksum,
        }


@dataclass
class BusinessEvent:
    """Event emitted during process lifecycle state transitions."""

    event_id: str = field(default_factory=lambda: f"EVT-{uuid.uuid4().hex[:10].upper()}")
    event_type: EventType = EventType.PURCHASE_ORDER_CREATED
    entity_type: str = "GENERIC"
    entity_id: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    actor: str = "SYSTEM"
    source: str = "PROCESS_ENGINE"
    correlation_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert event instance to dictionary representation."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "timestamp": self.timestamp.isoformat(),
            "actor": self.actor,
            "source": self.source,
            "correlation_id": self.correlation_id,
            "metadata": self.metadata,
        }


__all__ = ["CorrelationContext", "AuditEvidence", "BusinessEvent"]
