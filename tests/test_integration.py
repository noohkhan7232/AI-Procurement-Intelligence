"""End-to-end integration tests for TransactionSimulator orchestrator."""

from src.simulation.config import ScaleProfile, SimulationConfig
from src.simulation.transaction_simulator import TransactionSimulator


def test_full_transaction_simulator_execution(tmp_path):
    config = SimulationConfig(
        scale_profile=ScaleProfile.SMALL,
        seed=999,
        enable_anomaly_injection=True,
    )
    simulator = TransactionSimulator(config=config)
    result = simulator.run_simulation()

    assert result.status in ("SUCCESS", "COMPLETED_WITH_FAILURES")
    assert result.metrics.total_records_generated > 0
    assert result.validation_report.overall_valid is True
    assert result.run_directory.exists()
    assert "purchase_orders" in result.exported_files
    assert "business_events" in result.exported_files
    assert "summary_report" in result.exported_files
