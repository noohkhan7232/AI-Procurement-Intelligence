"""Shared pytest fixtures for Phase 03.3 test suite."""

from datetime import datetime
import pytest

from src.simulation.config.process_config import MasterProcessConfig
from src.simulation.config.simulation_config import ScaleProfile, SimulationConfig
from src.simulation.engines import (
    AccountingTransactionEngine,
    AnomalyEngine,
    ApprovalWorkflowEngine,
    BusinessEventEngine,
    CurrencyEngine,
    TemporalEngine,
)
from src.simulation.master_data import MasterDataProvider


@pytest.fixture
def sim_config() -> SimulationConfig:
    """Fixture providing a deterministic small scale SimulationConfig."""
    return SimulationConfig(
        scale_profile=ScaleProfile.SMALL,
        seed=123,
        enable_anomaly_injection=True,
        overall_anomaly_rate=0.05,
    )


@pytest.fixture
def master_config() -> MasterProcessConfig:
    """Fixture providing default MasterProcessConfig."""
    return MasterProcessConfig()


@pytest.fixture
def master_pool(sim_config: SimulationConfig):
    """Fixture providing initialized MasterDataPool."""
    mdp = MasterDataProvider(sim_config)
    return mdp.load_or_synthesize()


@pytest.fixture
def temporal_engine(sim_config: SimulationConfig) -> TemporalEngine:
    """Fixture providing TemporalEngine."""
    return TemporalEngine(sim_config)


@pytest.fixture
def currency_engine(sim_config: SimulationConfig) -> CurrencyEngine:
    """Fixture providing CurrencyEngine."""
    return CurrencyEngine(sim_config)


@pytest.fixture
def event_engine() -> BusinessEventEngine:
    """Fixture providing BusinessEventEngine."""
    return BusinessEventEngine()


@pytest.fixture
def approval_engine(sim_config: SimulationConfig, master_pool) -> ApprovalWorkflowEngine:
    """Fixture providing ApprovalWorkflowEngine."""
    return ApprovalWorkflowEngine(sim_config, master_pool)


@pytest.fixture
def accounting_engine(master_pool) -> AccountingTransactionEngine:
    """Fixture providing AccountingTransactionEngine."""
    return AccountingTransactionEngine(master_pool)


@pytest.fixture
def anomaly_engine(sim_config: SimulationConfig) -> AnomalyEngine:
    """Fixture providing AnomalyEngine."""
    return AnomalyEngine(sim_config)
