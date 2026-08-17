"""Anomaly domain models for Phase 03.3 Enterprise Transaction Simulation Engine.

Provides metadata records for tracking injected audit anomalies.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from src.simulation.domain.enums import AnomalySeverity, AnomalyType


@dataclass
class AnomalyRecord:
    """Metadata container for an injected, controlled anomaly."""

    anomaly_id: str
    anomaly_type: AnomalyType
    severity: AnomalySeverity
    entity_type: str
    entity_id: str
    injection_timestamp: datetime
    injection_rule: str
    expected_detection_rule: str
    description: str
    correlation_id: str
    metadata: dict


__all__ = ["AnomalyRecord"]
