"""Business Event Engine for Phase 03.3 Enterprise Transaction Simulation Engine.

Manages emission, correlation, and lifecycle tracking of BusinessEvent objects.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from src.simulation.domain.enums import EventType
from src.simulation.domain.models.base import BusinessEvent, CorrelationContext


class BusinessEventEngine:
    """Emits, collects, and correlates business events across all processes."""

    def __init__(self) -> None:
        """Initialize event engine."""
        self._events: list[BusinessEvent] = []

    def create_event(
        self,
        event_type: EventType,
        entity_type: str,
        entity_id: str,
        correlation_id: str,
        timestamp: datetime | None = None,
        actor: str = "SYSTEM",
        source: str = "PROCESS_ENGINE",
        metadata: dict[str, Any] | None = None,
    ) -> BusinessEvent:
        """Emit a new business event."""
        event = BusinessEvent(
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            correlation_id=correlation_id,
            timestamp=timestamp or datetime.now(),
            actor=actor,
            source=source,
            metadata=metadata or {},
        )
        self._events.append(event)
        return event

    def get_events(self) -> list[BusinessEvent]:
        """Return all emitted events."""
        return self._events

    def get_events_for_correlation(self, correlation_id: str) -> list[BusinessEvent]:
        """Filter events belonging to a specific correlation trace."""
        return [e for e in self._events if e.correlation_id == correlation_id]

    def clear(self) -> None:
        """Reset internal event buffer."""
        self._events.clear()


__all__ = ["BusinessEventEngine"]
