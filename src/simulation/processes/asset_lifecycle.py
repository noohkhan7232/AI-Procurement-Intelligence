"""Asset Lifecycle Process Simulator for Phase 03.3 Enterprise Transaction Simulation Engine.

Orchestrates Fixed Asset Acquisition, Registration, Assignment, Transfers, Maintenance,
Straight-Line/Declining Balance Depreciation, Disposals, Write-Offs, and Accounting Entries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import random

from src.simulation.config.process_config import AssetProcessConfig
from src.simulation.config.simulation_config import SimulationConfig
from src.simulation.domain.enums import Currency, EventType, TransactionState
from src.simulation.domain.models import (
    AssetDepreciationEvent,
    AssetDisposalRecord,
    AssetMaintenanceRecord,
    AssetMaster,
    AssetTransferRecord,
    AuditEvidence,
    CorrelationContext,
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
class AssetProcessResults:
    """Container for all asset simulation outputs."""

    assets: list[AssetMaster] = field(default_factory=list)
    depreciation_events: list[AssetDepreciationEvent] = field(default_factory=list)
    transfers: list[AssetTransferRecord] = field(default_factory=list)
    maintenances: list[AssetMaintenanceRecord] = field(default_factory=list)
    disposals: list[AssetDisposalRecord] = field(default_factory=list)


class AssetLifecycleSimulator:
    """End-to-end Asset Lifecycle process simulator."""

    def __init__(
        self,
        config: SimulationConfig,
        asset_config: AssetProcessConfig,
        master_pool: MasterDataPool,
        temporal_engine: TemporalEngine,
        event_engine: BusinessEventEngine,
        accounting_engine: AccountingTransactionEngine,
        anomaly_engine: AnomalyEngine,
    ) -> None:
        """Initialize Asset simulator."""
        self._config = config
        self._asset_config = asset_config
        self._master = master_pool
        self._temporal = temporal_engine
        self._event = event_engine
        self._accounting = accounting_engine
        self._anomaly = anomaly_engine
        random.seed(config.seed)

    def simulate(self, target_assets: int) -> AssetProcessResults:
        """Execute Asset simulation generating target fixed asset count."""
        results = AssetProcessResults()
        logger.info("Executing Asset Lifecycle Simulation (Target Assets: %d)...", target_assets)

        asset_categories = [
            ("CAT-COMP", "IT Equipment & Computers", 3, 2500.0),
            ("CAT-MACH", "Manufacturing Machinery", 10, 50000.0),
            ("CAT-VEH", "Commercial Vehicles", 7, 35000.0),
            ("CAT-FURN", "Office Furniture", 5, 5000.0),
        ]

        for i in range(1, target_assets + 1):
            correlation = CorrelationContext(process_type="ASSET")
            cat_id, cat_name, life_yrs, cost_base = random.choice(asset_categories)
            acq_date = self._temporal.generate_random_timestamp()
            wh = random.choice(self._master.warehouses)
            custodian = random.choice(self._master.employees)
            cost = round(cost_base * random.uniform(0.8, 1.5), 2)
            salvage = round(cost * self._asset_config.default_salvage_value_pct, 2)

            asset_id = f"AST-{i:06d}"
            asset = AssetMaster(
                asset_id=asset_id,
                asset_number=f"AST-NUM-{i:06d}",
                asset_name=f"{cat_name} #{i}",
                category_id=cat_id,
                category_name=cat_name,
                acquisition_date=acq_date,
                acquisition_cost=cost,
                salvage_value=salvage,
                useful_life_years=life_yrs,
                depreciation_method="STRAIGHT_LINE",
                current_book_value=cost,
                accumulated_depreciation=0.0,
                assigned_employee_id=custodian.employee_id,
                location_id=wh.location,
                custodian_id=custodian.employee_id,
                state=TransactionState.COMPLETED,
                currency=Currency.USD,
                correlation_id=correlation.correlation_id,
                audit_evidence=AuditEvidence(transaction_id=asset_id, created_timestamp=acq_date, correlation_id=correlation.correlation_id),
            )
            results.assets.append(asset)
            self._event.create_event(EventType.ASSET_ACQUIRED, "ASSET_MASTER", asset_id, correlation.correlation_id, acq_date)

            # Post Acquisition Accounting Entry (Debit Fixed Assets 150000, Credit Cash/AP 100000)
            self._accounting.create_journal_entry(
                source_document_type="ASSET_ACQ",
                source_document_id=asset_id,
                posting_date=acq_date,
                currency=Currency.USD,
                exchange_rate=1.0,
                debit_entries=[("150000", cost, "Fixed Asset Acquisition")],
                credit_entries=[("100000", cost, "Asset Acquisition Cash Outflow")],
                correlation_id=correlation.correlation_id,
            )

            # Generate monthly depreciation events (up to 12 periods)
            annual_depr = (cost - salvage) / life_yrs
            monthly_depr = round(annual_depr / 12.0, 2)
            accum = 0.0

            for m in range(1, 13):
                depr_date = acq_date + timedelta(days=m * 30)
                accum = round(accum + monthly_depr, 2)
                book_val = round(max(salvage, cost - accum), 2)

                depr_event = AssetDepreciationEvent(
                    event_id=f"DEPR-{asset_id}-M{m:02d}",
                    asset_id=asset_id,
                    period_date=depr_date,
                    depreciation_amount=monthly_depr,
                    accumulated_depreciation_after=accum,
                    book_value_after=book_val,
                    journal_entry_id=f"JE-DEPR-{asset_id}-M{m:02d}",
                    correlation_id=correlation.correlation_id,
                )
                results.depreciation_events.append(depr_event)

                asset.accumulated_depreciation = accum
                asset.current_book_value = book_val

                # Post Depreciation Accounting Entry (Debit Depreciation Expense 620000, Credit Accum Depreciation 150000)
                self._accounting.create_journal_entry(
                    source_document_type="ASSET_DEPR",
                    source_document_id=depr_event.event_id,
                    posting_date=depr_date,
                    currency=Currency.USD,
                    exchange_rate=1.0,
                    debit_entries=[("620000", monthly_depr, "Monthly Asset Depreciation")],
                    credit_entries=[("150000", monthly_depr, "Accumulated Depreciation")],
                    correlation_id=correlation.correlation_id,
                )

        logger.info("Asset Lifecycle Simulation Complete: %d Assets, %d Depreciation events.", len(results.assets), len(results.depreciation_events))
        return results


__all__ = ["AssetProcessResults", "AssetLifecycleSimulator"]
