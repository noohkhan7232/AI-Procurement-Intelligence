"""Duplicate row detection and remediation.

Provides :class:`DuplicateHandler`, which detects exact-match duplicate rows
(optionally scoped to a subset of columns) and removes them according to a
configurable keep strategy.

Typical usage:
    >>> from src.cleaning.duplicate_handler import (
    ...     DuplicateHandler, DuplicateHandlingConfig, DuplicateKeepStrategy,
    ... )
    >>> from src.utils.cleaning_logger import CleaningLogger
    >>> handler = DuplicateHandler(CleaningLogger(__name__))
    >>> config = DuplicateHandlingConfig(keep_strategy=DuplicateKeepStrategy.FIRST)
    >>> cleaned_df, report = handler.remove(df, dataset_name="vendors", config=config)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

from src.utils.cleaning_logger import CleaningLogger

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Module identifier used in structured log entries.
MODULE_NAME: str = "DuplicateHandler"


class DuplicateKeepStrategy(str, Enum):
    """Which occurrence(s) of a duplicate group to retain."""

    FIRST = "FIRST"
    LAST = "LAST"
    NONE = "NONE"

    def to_pandas_keep(self) -> str | bool:
        """Translate this strategy into the value expected by pandas.

        Returns:
            ``'first'``, ``'last'``, or ``False`` — the values accepted by
            ``DataFrame.duplicated(keep=...)`` / ``drop_duplicates(keep=...)``.
        """
        mapping: dict[DuplicateKeepStrategy, str | bool] = {
            DuplicateKeepStrategy.FIRST: "first",
            DuplicateKeepStrategy.LAST: "last",
            DuplicateKeepStrategy.NONE: False,
        }
        return mapping[self]


@dataclass
class DuplicateHandlingConfig:
    """Configuration controlling duplicate detection and removal.

    Attributes:
        subset: Column names considered when identifying duplicates. If
            ``None``, every column is considered (a full-row match).
        keep_strategy: Which occurrence(s) to retain among each duplicate
            group. Defaults to :attr:`DuplicateKeepStrategy.FIRST`.
    """

    subset: list[str] | None = None
    keep_strategy: DuplicateKeepStrategy = DuplicateKeepStrategy.FIRST


class DuplicateHandler:
    """Detect and remove duplicate rows from tabular datasets.

    Single responsibility: duplicate row detection/removal only. Missing
    values, type coercion, and business rules are handled by their own
    dedicated modules.
    """

    def __init__(self, cleaning_logger: CleaningLogger) -> None:
        """Initialize the handler.

        Args:
            cleaning_logger: Shared structured logger used to record every
                duplicate-removal decision for later inclusion in reports.
        """
        self._logger = cleaning_logger

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def detect(
        self, dataframe: pd.DataFrame, *, subset: list[str] | None = None
    ) -> dict[str, Any]:
        """Identify duplicate rows without modifying the data.

        Args:
            dataframe: The dataset to inspect.
            subset: Column names considered when identifying duplicates. If
                ``None``, every column is considered.

        Returns:
            A dictionary with the duplicate row count, percentage, and the
            row indices (as strings) that are flagged as duplicates.
        """
        row_count = dataframe.shape[0]
        duplicate_mask = dataframe.duplicated(subset=subset, keep="first")
        duplicate_count = int(duplicate_mask.sum())

        return {
            "row_count": row_count,
            "duplicate_count": duplicate_count,
            "duplicate_percentage": self._safe_percentage(
                duplicate_count, row_count
            ),
            "subset_columns": subset,
            "duplicate_row_indices": [
                str(idx) for idx in dataframe.index[duplicate_mask]
            ],
        }

    def remove(
        self,
        dataframe: pd.DataFrame,
        *,
        dataset_name: str,
        config: DuplicateHandlingConfig | None = None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Remove duplicate rows according to the given configuration.

        Args:
            dataframe: The dataset to deduplicate. Not modified in place; a
                cleaned copy is returned.
            dataset_name: Human-readable dataset identifier, used for
                logging and embedded in the returned report.
            config: The deduplication configuration. If ``None``, defaults
                to a fresh :class:`DuplicateHandlingConfig` (full-row match,
                keep first).

        Returns:
            A tuple of ``(cleaned_dataframe, report)`` where ``report`` is a
            JSON-serializable dictionary describing the outcome.
        """
        config = config or DuplicateHandlingConfig()
        rows_before = dataframe.shape[0]

        detection = self.detect(dataframe, subset=config.subset)

        cleaned_df = dataframe.drop_duplicates(
            subset=config.subset, keep=config.keep_strategy.to_pandas_keep()
        ).reset_index(drop=True)

        rows_after = cleaned_df.shape[0]
        duplicates_removed = rows_before - rows_after

        report = {
            "dataset_name": dataset_name,
            "rows_before": rows_before,
            "rows_after": rows_after,
            "duplicates_detected": detection["duplicate_count"],
            "duplicates_removed": duplicates_removed,
            "duplicate_percentage_before": detection["duplicate_percentage"],
            "keep_strategy": config.keep_strategy.value,
            "subset_columns": config.subset,
        }

        self._logger.log_operation(
            dataset_name=dataset_name,
            module=MODULE_NAME,
            operation="remove_duplicates",
            message=(
                f"Removed {duplicates_removed} duplicate row(s) "
                f"(keep={config.keep_strategy.value}); "
                f"rows {rows_before} -> {rows_after}."
            ),
            details=report,
        )

        return cleaned_df, report

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_percentage(numerator: int, denominator: int) -> float:
        """Compute a percentage while guarding against division by zero."""
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)


__all__ = ["DuplicateHandler", "DuplicateHandlingConfig", "DuplicateKeepStrategy"]
