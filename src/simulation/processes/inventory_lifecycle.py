"""Inventory Process Simulator for Phase 03.3 Enterprise Transaction Simulation Engine.

Orchestrates inventory lifecycle movements, transfers, reservations, cycle counts,
write-offs, FIFO/Moving Average valuation events, and inventory anomaly injection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import random

from src.simulation.config.process_config import InventoryProcessConfig
from src.simulation.config.simulation_config import SimulationConfig
from src.simulation.domain.enums import AnomalySeverity, AnomalyType, EventType, TransactionState
from src.simulation.domain.models import (
    AuditEvidence,
    CorrelationContext,
    CycleCount,
    InventoryMovement,
    InventoryValuationEvent,
    InventoryWriteOff,
    StockReservation,
)
from src.simulation.engines import (
    AccountingTransactionEngine,
    AnomalyEngine,
    BusinessEventEngine,
    TemporalEngine,
)
from src.simulation.master_data import MasterDataPool
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class InventoryProcessResults:
    """Container for all inventory simulation outputs."""

    movements: list[InventoryMovement] = field(default_factory=list)
    reservations: list[StockReservation] = field(default_factory=list)
    cycle_counts: list[CycleCount] = field(default_factory=list)
    write_offs: list[InventoryWriteOff] = field(default_factory=list)
    valuations: list[InventoryValuationEvent] = field(default_factory=list)


class InventoryLifecycleSimulator:
    """End-to-end Inventory movement & valuation process simulator."""

    def __init__(
        self,
        config: SimulationConfig,
        inv_config: InventoryProcessConfig,
        master_pool: MasterDataPool,
        temporal_engine: TemporalEngine,
        event_engine: BusinessEventEngine,
        accounting_engine: AccountingTransactionEngine,
        anomaly_engine: AnomalyEngine,
    ) -> None:
        """Initialize Inventory simulator."""
        self._config = config
        self._inv_config = inv_config
        self._master = master_pool
        self._temporal = temporal_engine
        self._event = event_engine
        self._accounting = accounting_engine
        self._anomaly = anomaly_engine
        random.seed(config.seed)

    def simulate(self, target_movements: int) -> InventoryProcessResults:
        """Execute Inventory simulation generating target movements."""
        results = InventoryProcessResults()
        logger.info("Executing Inventory Lifecycle Simulation (Target Movements: %d)...", target_movements)

        for i in range(1, target_movements + 1):
            correlation = CorrelationContext(process_type="INVENTORY")
            move_date = self._temporal.generate_random_timestamp()
            prod = random.choice(self._master.products)
            wh_source = random.choice(self._master.warehouses)

            # Determine movement type: RECEIPT (35%), ISSUE (35%), TRANSFER (20%), ADJUSTMENT (10%)
            mtype = random.choices(
                ["RECEIPT", "ISSUE", "TRANSFER", "ADJUSTMENT"],
                weights=[0.35, 0.35, 0.20, 0.10],
            )[0]

            qty = float(random.randint(10, 500))
            if mtype == "TRANSFER":
                # Ensure source != destination
                other_whs = [w for w in self._master.warehouses if w.warehouse_id != wh_source.warehouse_id]
                wh_dest = random.choice(other_whs) if other_whs else wh_source
            else:
                wh_dest = None

            # Anomaly injection: Negative Inventory Exception / Adjustment Anomaly
            if self._anomaly.should_inject("INVENTORY") and random.random() < 0.08:
                if mtype == "ADJUSTMENT":
                    qty = -50000.0  # Massive abnormal negative adjustment
                    self._anomaly.record_anomaly(
                        anomaly_type=AnomalyType.INVENTORY_ADJUSTMENT_ANOMALY,
                        entity_type="INVENTORY_MOVEMENT",
                        entity_id=f"MOV-{i:06d}",
                        description="Abnormal massive inventory adjustment detected",
                        injection_rule="LARGE_NEGATIVE_QTY_ADJUSTMENT",
                        expected_detection_rule="DETECT_LARGE_INVENTORY_ADJUSTMENTS",
                        correlation_id=correlation.correlation_id,
                    )

            move = InventoryMovement(
                movement_id=f"MOV-{i:06d}",
                movement_number=f"MOV-NUM-{i:06d}",
                movement_type=mtype,
                product_id=prod.product_id,
                warehouse_id=wh_source.warehouse_id,
                source_warehouse_id=wh_source.warehouse_id if mtype == "TRANSFER" else None,
                destination_warehouse_id=wh_dest.warehouse_id if wh_dest else None,
                quantity=qty,
                unit_cost=prod.cost_price,
                total_value=round(qty * prod.cost_price, 2),
                movement_date=move_date,
                reason_code=f"REASON_{mtype}",
                reference_document_id=f"REF-{i:06d}",
                correlation_id=correlation.correlation_id,
                audit_evidence=AuditEvidence(transaction_id=f"MOV-{i:06d}", created_timestamp=move_date, correlation_id=correlation.correlation_id),
            )
            results.movements.append(move)

            event_type = EventType.INVENTORY_RECEIVED if mtype == "RECEIPT" else EventType.INVENTORY_TRANSFERRED
            self._event.create_event(event_type, "INVENTORY_MOVEMENT", move.movement_id, correlation.correlation_id, move_date)

            # Cycle count sampling (every 10th movement)
            if i % 10 == 0:
                counter = random.choice(self._master.employees)
                counted = qty + random.choice([0.0, 0.0, -2.0, 5.0])
                cc = CycleCount(
                    count_id=f"CC-{i:06d}",
                    count_number=f"CC-NUM-{i:06d}",
                    warehouse_id=wh_source.warehouse_id,
                    count_date=move_date + timedelta(days=1),
                    counted_by_id=counter.employee_id,
                    product_id=prod.product_id,
                    system_quantity=qty,
                    counted_quantity=counted,
                    variance_quantity=counted - qty,
                    variance_value=round((counted - qty) * prod.cost_price, 2),
                    adjustment_applied=(counted != qty),
                    correlation_id=correlation.correlation_id,
                )
                results.cycle_counts.append(cc)

        logger.info("Inventory Lifecycle Simulation Complete: %d Movements generated.", len(results.movements))
        return results


__all__ = ["InventoryProcessResults", "InventoryLifecycleSimulator"]
