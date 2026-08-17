"""Controlled Anomaly Injection Framework for Phase 03.3 Enterprise Transaction Simulation Engine.

Injects reproducible, statistically controlled, traceable audit anomalies across all
business processes (P2P, O2C, Inventory, Expense, Payroll, Asset, Accounting).
"""

from __future__ import annotations

from datetime import datetime
import random
import uuid

from src.simulation.config.simulation_config import SimulationConfig
from src.simulation.domain.enums import AnomalySeverity, AnomalyType
from src.simulation.domain.models.anomaly import AnomalyRecord
from src.utils.logger import get_logger

logger = get_logger(__name__)


class AnomalyEngine:
    """Injects controlled audit anomalies and tracks metadata for detection verification."""

    def __init__(self, config: SimulationConfig) -> None:
        """Initialize anomaly engine."""
        self._config = config
        self._enabled = config.enable_anomaly_injection
        self._anomaly_records: list[AnomalyRecord] = []
        random.seed(config.seed)

    def should_inject(self, domain: str) -> bool:
        """Determine if an anomaly should be injected based on domain configuration."""
        if not self._enabled:
            return False

        rate = self._config.per_domain_anomaly_rates.get(domain, self._config.overall_anomaly_rate)
        return random.random() < rate

    def record_anomaly(
        self,
        anomaly_type: AnomalyType,
        entity_type: str,
        entity_id: str,
        description: str,
        injection_rule: str,
        expected_detection_rule: str,
        correlation_id: str,
        severity: AnomalySeverity = AnomalySeverity.HIGH,
        metadata: dict | None = None,
    ) -> AnomalyRecord:
        """Create and buffer an anomaly tracking record."""
        record = AnomalyRecord(
            anomaly_id=f"ANOM-{uuid.uuid4().hex[:10].upper()}",
            anomaly_type=anomaly_type,
            severity=severity,
            entity_type=entity_type,
            entity_id=entity_id,
            injection_timestamp=datetime.now(),
            injection_rule=injection_rule,
            expected_detection_rule=expected_detection_rule,
            description=description,
            correlation_id=correlation_id,
            metadata=metadata or {},
        )
        self._anomaly_records.append(record)
        logger.info(
            "Injected Anomaly [%s] into %s:%s - %s",
            anomaly_type.value,
            entity_type,
            entity_id,
            description,
        )
        return record

    def get_anomaly_records(self) -> list[AnomalyRecord]:
        """Return all recorded anomaly metadata."""
        return self._anomaly_records

    def clear(self) -> None:
        """Clear anomaly records buffer."""
        self._anomaly_records.clear()


__all__ = ["AnomalyEngine"]
