"""Phase 03.3 — Enterprise Transaction Simulation Executor CLI.

Command-line entry point to configure, execute, validate, and export transaction simulations.

Usage:
    $ python -m src.simulation.simulation_executor
    $ python -m src.simulation.simulation_executor --scale medium --seed 123
"""

from __future__ import annotations

import argparse
import sys
import time

from src.simulation.config.simulation_config import ScaleProfile, SimulationConfig
from src.simulation.transaction_simulator import TransactionSimulator
from src.utils.logger import get_logger

logger = get_logger(__name__)


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="EAAP Enterprise Transaction Simulation Engine CLI")
    parser.add_argument(
        "--scale",
        type=str,
        default="small",
        choices=["small", "medium", "large", "enterprise"],
        help="Simulation scale profile (default: small)",
    )
    parser.add_argument("--seed", type=int, default=42, help="Deterministic random seed (default: 42)")
    parser.add_argument("--no-anomalies", action="store_true", help="Disable audit anomaly injection")
    return parser.parse_args()


def main() -> int:
    """CLI execution entry point."""
    print("=" * 60)
    print("AI PROCUREMENT INTELLIGENCE PLATFORM")
    print("Phase 03.3 — Enterprise Transaction Simulation Engine")
    print("=" * 60)

    try:
        args = parse_args()
        scale_enum = ScaleProfile(args.scale.lower())

        config = SimulationConfig(
            scale_profile=scale_enum,
            seed=args.seed,
            enable_anomaly_injection=(not args.no_anomalies),
        )

        simulator = TransactionSimulator(config=config)
        result = simulator.run_simulation()

        print("\n" + "=" * 60)
        print("SIMULATION EXECUTION SUMMARY")
        print("=" * 60)
        print(f"Status           : {result.status}")
        print(f"Run Timestamp    : {result.run_timestamp}")
        print(f"Scale Profile    : {result.metrics.scale_profile}")
        print(f"Duration         : {result.metrics.duration_seconds:.2f}s")
        print(f"Total Records    : {result.metrics.total_records_generated}")
        print(f"TPS              : {result.metrics.tps:.2f}")
        print(f"Anomalies        : {result.metrics.anomalies_injected}")
        print(f"Overall Valid    : {result.validation_report.overall_valid}")
        print(f"Output Directory : {result.run_directory}")
        print("=" * 60)

        if result.status == "SUCCESS":
            return 0
        return 1

    except Exception as exc:
        logger.exception("Critical failure during transaction simulation execution: %s", exc)
        print(f"\nCRITICAL FAILURE: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
