"""Integration tests for all 6 business process simulators."""

from src.simulation.config.process_config import MasterProcessConfig
from src.simulation.processes import (
    AssetLifecycleSimulator,
    ExpenseToReimbursementSimulator,
    HireToPayrollSimulator,
    InventoryLifecycleSimulator,
    OrderToCashSimulator,
    ProcureToPaySimulator,
)


def test_p2p_simulator(
    sim_config, master_config, master_pool, temporal_engine, currency_engine, event_engine, approval_engine, accounting_engine, anomaly_engine
):
    p2p_sim = ProcureToPaySimulator(
        sim_config, master_config.p2p, master_pool, temporal_engine, currency_engine, event_engine, approval_engine, accounting_engine, anomaly_engine
    )
    results = p2p_sim.simulate(target_orders=5)
    assert len(results.purchase_orders) == 5
    assert len(results.requisitions) == 5
    assert len(results.goods_receipts) == 5
    assert len(results.invoices) >= 5


def test_o2c_simulator(
    sim_config, master_config, master_pool, temporal_engine, currency_engine, event_engine, accounting_engine, anomaly_engine
):
    o2c_sim = OrderToCashSimulator(
        sim_config, master_config.o2c, master_pool, temporal_engine, currency_engine, event_engine, accounting_engine, anomaly_engine
    )
    results = o2c_sim.simulate(target_orders=5)
    assert len(results.sales_orders) == 5
    assert len(results.shipments) == 5
    assert len(results.deliveries) == 5
    assert len(results.invoices) == 5
    assert len(results.payments) == 5


def test_inventory_simulator(
    sim_config, master_config, master_pool, temporal_engine, event_engine, accounting_engine, anomaly_engine
):
    inv_sim = InventoryLifecycleSimulator(
        sim_config, master_config.inventory, master_pool, temporal_engine, event_engine, accounting_engine, anomaly_engine
    )
    results = inv_sim.simulate(target_movements=10)
    assert len(results.movements) == 10
    assert len(results.cycle_counts) >= 1


def test_expense_simulator(
    sim_config, master_config, master_pool, temporal_engine, currency_engine, event_engine, approval_engine, accounting_engine, anomaly_engine
):
    exp_sim = ExpenseToReimbursementSimulator(
        sim_config, master_config.expense, master_pool, temporal_engine, currency_engine, event_engine, approval_engine, accounting_engine, anomaly_engine
    )
    results = exp_sim.simulate(target_claims=5)
    assert len(results.claims) == 5
    assert len(results.reimbursements) >= 1


def test_payroll_simulator(
    sim_config, master_config, master_pool, temporal_engine, event_engine, approval_engine, accounting_engine, anomaly_engine
):
    payroll_sim = HireToPayrollSimulator(
        sim_config, master_config.payroll, master_pool, temporal_engine, event_engine, approval_engine, accounting_engine, anomaly_engine
    )
    results = payroll_sim.simulate(target_periods=2)
    assert len(results.periods) == 2
    assert len(results.records) == len(master_pool.employees) * 2


def test_asset_simulator(
    sim_config, master_config, master_pool, temporal_engine, event_engine, accounting_engine, anomaly_engine
):
    asset_sim = AssetLifecycleSimulator(
        sim_config, master_config.asset, master_pool, temporal_engine, event_engine, accounting_engine, anomaly_engine
    )
    results = asset_sim.simulate(target_assets=4)
    assert len(results.assets) == 4
    assert len(results.depreciation_events) == 4 * 12
