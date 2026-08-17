"""CSV & Parquet Exporter for Phase 03.3 Enterprise Transaction Simulation Engine.

Converts simulation entities into clean DataFrames and persists them as CSV and Parquet files.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from src.utils.logger import get_logger

logger = get_logger(__name__)


class CSVExporter:
    """Persists simulation domain objects as CSV and Parquet datasets."""

    def export_dataset(
        self,
        dataset_name: str,
        records: list[Any],
        output_dir: Path,
        export_parquet: bool = True,
    ) -> dict[str, Path]:
        """Export a list of records/dataclasses to CSV (and Parquet if supported)."""
        output_dir.mkdir(parents=True, exist_ok=True)
        csv_path = output_dir / f"{dataset_name}.csv"
        parquet_path = output_dir / f"{dataset_name}.parquet"

        if not records:
            logger.warning("Empty records list for dataset '%s'; creating empty CSV.", dataset_name)
            df = pd.DataFrame()
            df.to_csv(csv_path, index=False)
            return {"csv": csv_path}

        rows = []
        for r in records:
            if is_dataclass(r) and not isinstance(r, type):
                d = asdict(r)
                # Flatten complex fields (e.g. audit_evidence, items lists)
                for k, v in list(d.items()):
                    if isinstance(v, dict):
                        for sub_k, sub_v in v.items():
                            d[f"{k}_{sub_k}"] = sub_v
                        del d[k]
                    elif isinstance(v, list):
                        d[k] = len(v)  # count of items
                rows.append(d)
            elif isinstance(r, dict):
                rows.append(r)
            elif hasattr(r, "to_dict"):
                rows.append(r.to_dict())

        df = pd.DataFrame(rows)
        df.to_csv(csv_path, index=False)
        logger.info("Exported CSV for '%s' (%d rows) -> %s", dataset_name, len(df), csv_path)

        result = {"csv": csv_path}

        if export_parquet:
            try:
                # Convert object columns to string for parquet compatibility
                parquet_df = df.copy()
                for col in parquet_df.columns:
                    if parquet_df[col].dtype == "object":
                        parquet_df[col] = parquet_df[col].astype(str)
                parquet_df.to_parquet(parquet_path, index=False)
                result["parquet"] = parquet_path
                logger.info("Exported Parquet for '%s' -> %s", dataset_name, parquet_path)
            except Exception as exc:
                logger.warning("Parquet export skipped for '%s': %s", dataset_name, exc)

        return result


__all__ = ["CSVExporter"]
