"""Observability & Execution Statistics for Phase 03.3 Enterprise Transaction Simulation Engine.

Tracks runtime duration, transactions generated, TPS (transactions per second), anomaly counts,
and validation metrics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import time
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SimulationMetrics:
    """Metrics container tracking simulation execution statistics."""

    start_timestamp: datetime = field(default_factory=datetime.now)
    end_timestamp: datetime | None = None
    duration_seconds: float = 0.0
    scale_profile: str = "small"
    total_records_generated: int = 0
    records_by_entity: dict[str, int] = field(default_factory=dict)
    tps: float = 0.0
    anomalies_injected: int = 0
    anomalies_by_type: dict[str, int] = field(default_factory=dict)
    validation_checks_total: int = 0
    validation_checks_passed: int = 0
    validation_checks_failed: int = 0
    overall_valid: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary for JSON report serialization."""
        return {
            "start_timestamp": self.start_timestamp.isoformat(),
            "end_timestamp": self.end_timestamp.isoformat() if self.end_timestamp else None,
            "duration_seconds": round(self.duration_seconds, 4),
            "scale_profile": self.scale_profile,
            "total_records_generated": self.total_records_generated,
            "tps": round(self.tps, 2),
            "records_by_entity": self.records_by_entity,
            "anomalies_injected": self.anomalies_injected,
            "anomalies_by_type": self.anomalies_by_type,
            "validation": {
                "total_checks": self.validation_checks_total,
                "passed_checks": self.validation_checks_passed,
                "failed_checks": self.validation_checks_failed,
                "overall_valid": self.overall_valid,
            },
        }


class MetricsCollector:
    """Collects runtime performance, volume, and anomaly statistics."""

    def __init__(self) -> None:
        """Initialize metrics collector."""
        self._start_time = time.perf_counter()
        self.metrics = SimulationMetrics()

    def record_entities(self, entity_name: str, count: int) -> None:
        """Record count of generated entities."""
        self.metrics.records_by_entity[entity_name] = count
        self.metrics.total_records_generated += count

    def record_anomalies(self, anomaly_records: list[Any]) -> None:
        """Record anomaly metrics."""
        self.metrics.anomalies_injected = len(anomaly_records)
        for r in anomaly_records:
            atype = r.anomaly_type.value if hasattr(r.anomaly_type, "value") else str(r.anomaly_type)
            self.metrics.anomalies_by_type[atype] = self.metrics.anomalies_by_type.get(atype, 0) + 1

    def record_validation(self, validation_report: Any) -> None:
        """Record validation results."""
        self.metrics.validation_checks_total = validation_report.total_checks
        self.metrics.validation_checks_passed = validation_report.passed_checks
        self.metrics.validation_checks_failed = validation_report.failed_checks
        self.metrics.overall_valid = validation_report.overall_valid

    def finalize(self) -> SimulationMetrics:
        """Finalize timer and compute TPS."""
        elapsed = time.perf_counter() - self._start_time
        self.metrics.end_timestamp = datetime.now()
        self.metrics.duration_seconds = elapsed
        self.metrics.tps = (self.metrics.total_records_generated / elapsed) if elapsed > 0 else 0.0
        return self.metrics

    def persist_summary_report(self, output_dir: Path, timestamp_str: str) -> Path:
        """Persist execution summary JSON report."""
        output_dir.mkdir(parents=True, exist_ok=True)
        summary_path = output_dir / f"simulation_summary_{timestamp_str}.json"

        with summary_path.open("w", encoding="utf-8") as f:
            json.dump(self.metrics.to_dict(), f, indent=2)

        logger.info("Persisted simulation summary report -> %s", summary_path)
        return summary_path


__all__ = ["SimulationMetrics", "MetricsCollector"]
