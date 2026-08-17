"""Unit tests for simulation configuration system."""

from src.simulation.config import SCALE_PROFILES, MasterProcessConfig, ScaleTarget, SimulationConfig
from src.simulation.domain.enums import ScaleProfile


def test_simulation_config_defaults():
    config = SimulationConfig()
    assert config.scale_profile == ScaleProfile.SMALL
    assert config.seed == 42
    assert config.enable_anomaly_injection is True
    assert config.overall_anomaly_rate == 0.05


def test_scale_targets():
    config = SimulationConfig(scale_profile=ScaleProfile.MEDIUM)
    target = config.get_scale_target()
    assert target.employees == 250
    assert target.purchase_orders == 1000
    assert target.sales_orders == 1500


def test_master_process_config():
    p_config = MasterProcessConfig()
    assert p_config.p2p.price_variance_tolerance_pct == 2.0
    assert p_config.expense.receipt_required_threshold == 25.0
    assert p_config.payroll.overtime_multiplier == 1.5
