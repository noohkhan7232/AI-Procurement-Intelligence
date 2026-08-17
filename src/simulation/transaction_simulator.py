"""Master Orchestrator for Phase 03.3 Enterprise Transaction Simulation Engine.

Coordinates master data loading, engine initialization, business process simulation
(P2P, O2C, Inventory, Expense, Payroll, Asset), validation, export, and observability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
import shutil
from typing import Any

from src.config.paths import get_project_paths
from src.simulation.config.process_config import MasterProcessConfig
from src.simulation.config.simulation_config import SimulationConfig
from src.simulation.engines import (
    AccountingTransactionEngine,
    AnomalyEngine,
    ApprovalWorkflowEngine,
    BusinessEventEngine,
    CurrencyEngine,
    TemporalEngine,
)
from src.simulation.export import CSVExporter, PostgresExporter
from src.simulation.master_data import MasterDataProvider
from src.simulation.observability import MetricsCollector, SimulationMetrics
from src.simulation.processes import (
    AssetLifecycleSimulator,
    ExpenseToReimbursementSimulator,
    HireToPayrollSimulator,
    InventoryLifecycleSimulator,
    OrderToCashSimulator,
    ProcureToPaySimulator,
)
from src.simulation.validation import TransactionValidator, ValidationReport
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class SimulationRunResult:
    """Outcome container for a complete simulation execution run."""

    status: str
    run_timestamp: str
    run_directory: Path
    metrics: SimulationMetrics
    validation_report: ValidationReport
    exported_files: dict[str, Path] = field(default_factory=dict)


class TransactionSimulator:
    """Master orchestrator for Phase 03.3 Enterprise Transaction Simulation Engine."""

    def __init__(
        self,
        config: SimulationConfig | None = None,
        process_config: MasterProcessConfig | None = None,
        run_timestamp: str | None = None,
    ) -> None:
        """Initialize simulator with configuration and path resolution."""
        self._config = config or SimulationConfig()
        self._process_config = process_config or MasterProcessConfig()
        self._paths = get_project_paths()
        self._run_timestamp = run_timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
        self._run_dir = self._paths.simulation_runs_dir / self._run_timestamp
        self._latest_dir = self._paths.simulation_latest_dir

    def run_simulation(self) -> SimulationRunResult:
        """Execute complete transaction simulation lifecycle."""
        collector = MetricsCollector()
        collector.metrics.scale_profile = self._config.scale_profile.value
        logger.info("Starting Enterprise Transaction Simulation (Run: %s)...", self._run_timestamp)

        # 1. Master Data Provider
        mdp = MasterDataProvider(self._config)
        master_pool = mdp.load_or_synthesize()

        # 2. Engines Initialization
        temporal = TemporalEngine(self._config)
        currency = CurrencyEngine(self._config)
        event_engine = BusinessEventEngine()
        approval = ApprovalWorkflowEngine(self._config, master_pool)
        accounting = AccountingTransactionEngine(master_pool)
        anomaly = AnomalyEngine(self._config)

        # Target volumes derived from scale profile
        target = self._config.get_scale_target()

        # 3. Process Simulators Execution
        p2p_sim = ProcureToPaySimulator(
            self._config, self._process_config.p2p, master_pool, temporal, currency, event_engine, approval, accounting, anomaly
        )
        p2p_results = p2p_sim.simulate(target.purchase_orders)

        o2c_sim = OrderToCashSimulator(
            self._config, self._process_config.o2c, master_pool, temporal, currency, event_engine, accounting, anomaly
        )
        o2c_results = o2c_sim.simulate(target.sales_orders)

        inv_sim = InventoryLifecycleSimulator(
            self._config, self._process_config.inventory, master_pool, temporal, event_engine, accounting, anomaly
        )
        inv_results = inv_sim.simulate(target.purchase_orders * 2)

        exp_sim = ExpenseToReimbursementSimulator(
            self._config, self._process_config.expense, master_pool, temporal, currency, event_engine, approval, accounting, anomaly
        )
        exp_results = exp_sim.simulate(target.expense_claims)

        payroll_sim = HireToPayrollSimulator(
            self._config, self._process_config.payroll, master_pool, temporal, event_engine, approval, accounting, anomaly
        )
        payroll_results = payroll_sim.simulate(target.payroll_periods)

        asset_sim = AssetLifecycleSimulator(
            self._config, self._process_config.asset, master_pool, temporal, event_engine, accounting, anomaly
        )
        asset_results = asset_sim.simulate(target.fixed_assets)

        # Collect Journal Entries & Anomaly Records
        all_events = event_engine.get_events()
        all_anomalies = anomaly.get_anomaly_records()

        # 4. Validation Pipeline
        validator = TransactionValidator()
        validation_report = validator.validate_simulation_data(
            p2p_results, o2c_results, inv_results, exp_results, payroll_results, asset_results, [], all_anomalies
        )

        # Record metrics
        collector.record_entities("purchase_orders", len(p2p_results.purchase_orders))
        collector.record_entities("supplier_invoices", len(p2p_results.invoices))
        collector.record_entities("supplier_payments", len(p2p_results.payments))
        collector.record_entities("sales_orders", len(o2c_results.sales_orders))
        collector.record_entities("customer_invoices", len(o2c_results.invoices))
        collector.record_entities("customer_payments", len(o2c_results.payments))
        collector.record_entities("inventory_movements", len(inv_results.movements))
        collector.record_entities("expense_claims", len(exp_results.claims))
        collector.record_entities("payroll_records", len(payroll_results.records))
        collector.record_entities("fixed_assets", len(asset_results.assets))
        collector.record_entities("business_events", len(all_events))
        collector.record_anomalies(all_anomalies)
        collector.record_validation(validation_report)

        metrics = collector.finalize()

        # 5. Export Pipeline
        csv_exporter = CSVExporter()
        self._run_dir.mkdir(parents=True, exist_ok=True)
        exported_files: dict[str, Path] = {}

        if self._config.export_csv:
            for dataset_name, records in [
                ("purchase_orders", p2p_results.purchase_orders),
                ("supplier_invoices", p2p_results.invoices),
                ("supplier_payments", p2p_results.payments),
                ("sales_orders", o2c_results.sales_orders),
                ("customer_invoices", o2c_results.invoices),
                ("customer_payments", o2c_results.payments),
                ("inventory_movements", inv_results.movements),
                ("expense_claims", exp_results.claims),
                ("payroll_records", payroll_results.records),
                ("fixed_assets", asset_results.assets),
                ("anomaly_metadata", all_anomalies),
                ("business_events", all_events),
            ]:
                res = csv_exporter.export_dataset(dataset_name, records, self._run_dir, self._config.export_parquet)
                if "csv" in res:
                    exported_files[dataset_name] = res["csv"]
                if "parquet" in res:
                    exported_files[f"{dataset_name}_parquet"] = res["parquet"]

        if self._config.export_postgres_sql:
            pg_exporter = PostgresExporter()
            ddl_file = pg_exporter.generate_postgres_ddl(self._run_dir)
            exported_files["postgres_ddl"] = ddl_file

        # Sync synthetic output dir and latest directory
        self._sync_latest_directory()

        # Save metrics summary
        summary_file = collector.persist_summary_report(self._run_dir, self._run_timestamp)
        exported_files["summary_report"] = summary_file

        status = "SUCCESS" if validation_report.overall_valid else "COMPLETED_WITH_FAILURES"
        logger.info("Simulation Run %s finished with status: %s", self._run_timestamp, status)

        return SimulationRunResult(
            status=status,
            run_timestamp=self._run_timestamp,
            run_directory=self._run_dir,
            metrics=metrics,
            validation_report=validation_report,
            exported_files=exported_files,
        )

    def _sync_latest_directory(self) -> None:
        """Mirror current run outputs to ``reports/simulation/latest/`` and ``data/synthetic/simulation/``."""
        if self._latest_dir.exists():
            shutil.rmtree(self._latest_dir)
        self._latest_dir.mkdir(parents=True, exist_ok=True)

        synth_dir = self._paths.simulation_data_dir
        synth_dir.mkdir(parents=True, exist_ok=True)

        for item in self._run_dir.iterdir():
            if item.is_file():
                shutil.copy2(item, self._latest_dir / item.name)
                if item.suffix in (".csv", ".parquet"):
                    shutil.copy2(item, synth_dir / item.name)


__all__ = ["SimulationRunResult", "TransactionSimulator"]
