"""Configurable missing-value analysis and remediation.

Provides :class:`MissingValueHandler`, which analyzes missing values in a
``pandas.DataFrame`` and remediates them using a per-column configurable
strategy: drop, mean, median, mode, forward-fill, or a caller-supplied
business-rule default value.

The handler makes no assumptions about column names or dataset identity —
every behavior is driven entirely by :class:`MissingValueHandlingConfig`,
making it reusable across any dataset the platform ever ingests.

Typical usage:
    >>> from src.cleaning.missing_value_handler import (
    ...     MissingValueHandler, MissingValueHandlingConfig, MissingValueStrategy,
    ... )
    >>> from src.utils.cleaning_logger import CleaningLogger
    >>> handler = MissingValueHandler(CleaningLogger(__name__))
    >>> config = MissingValueHandlingConfig(
    ...     default_strategy=MissingValueStrategy.MODE,
    ...     column_strategies={"price": MissingValueStrategy.MEDIAN},
    ... )
    >>> cleaned_df, report = handler.handle(df, dataset_name="vendors", config=config)
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
MODULE_NAME: str = "MissingValueHandler"


class MissingValueStrategy(str, Enum):
    """Supported strategies for remediating missing values in a column."""

    DROP = "DROP"
    MEAN = "MEAN"
    MEDIAN = "MEDIAN"
    MODE = "MODE"
    FORWARD_FILL = "FORWARD_FILL"
    BUSINESS_RULE = "BUSINESS_RULE"
    SKIP = "SKIP"


@dataclass
class MissingValueHandlingConfig:
    """Configuration controlling how missing values are handled.

    Attributes:
        default_strategy: The strategy applied to any column containing
            missing values that is not explicitly listed in
            ``column_strategies``. Defaults to :attr:`MissingValueStrategy.MODE`.
        column_strategies: Per-column strategy overrides, keyed by column
            name. Takes precedence over ``default_strategy``.
        business_rule_defaults: Fill values used when a column's strategy is
            :attr:`MissingValueStrategy.BUSINESS_RULE`, keyed by column name.
    """

    default_strategy: MissingValueStrategy = MissingValueStrategy.MODE
    column_strategies: dict[str, MissingValueStrategy] = field(default_factory=dict)
    business_rule_defaults: dict[str, Any] = field(default_factory=dict)


class MissingValueHandler:
    """Analyze and remediate missing values in tabular datasets.

    This class has a single responsibility: given a DataFrame and a
    :class:`MissingValueHandlingConfig`, produce a remediated DataFrame plus
    a structured report describing exactly what was done, column by column.
    """

    def __init__(self, cleaning_logger: CleaningLogger) -> None:
        """Initialize the handler.

        Args:
            cleaning_logger: Shared structured logger used to record every
                remediation decision for later inclusion in reports.
        """
        self._logger = cleaning_logger

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def analyze(self, dataframe: pd.DataFrame) -> dict[str, Any]:
        """Compute missing-value statistics without modifying the data.

        Args:
            dataframe: The dataset to analyze.

        Returns:
            A dictionary with per-column missing counts/percentages and a
            dataset-level total.
        """
        row_count = dataframe.shape[0]
        columns: dict[str, Any] = {}

        for column_name in dataframe.columns:
            missing_count = int(dataframe[column_name].isna().sum())
            columns[str(column_name)] = {
                "missing_count": missing_count,
                "missing_percentage": self._safe_percentage(missing_count, row_count),
            }

        total_missing_cells = int(dataframe.isna().sum().sum())
        total_cells = int(dataframe.size)

        return {
            "row_count": row_count,
            "total_missing_cells": total_missing_cells,
            "total_missing_percentage": self._safe_percentage(
                total_missing_cells, total_cells
            ),
            "columns": columns,
        }

    def handle(
        self,
        dataframe: pd.DataFrame,
        *,
        dataset_name: str,
        config: MissingValueHandlingConfig | None = None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Remediate missing values according to the given configuration.

        Args:
            dataframe: The dataset to remediate. Not modified in place; a
                cleaned copy is returned.
            dataset_name: Human-readable dataset identifier, used for
                logging and embedded in the returned report.
            config: The remediation configuration. If ``None``, defaults to
                a fresh :class:`MissingValueHandlingConfig` (mode-fill
                everywhere).

        Returns:
            A tuple of ``(cleaned_dataframe, report)`` where ``report`` is a
            JSON-serializable dictionary describing every action taken.
        """
        config = config or MissingValueHandlingConfig()
        working_df = dataframe.copy()
        rows_before = working_df.shape[0]

        before_missing = {
            str(col): int(working_df[col].isna().sum()) for col in working_df.columns
        }

        column_reports: dict[str, Any] = {}

        for column_name in list(working_df.columns):
            missing_before = before_missing[str(column_name)]
            if missing_before == 0:
                continue

            strategy = config.column_strategies.get(
                column_name, config.default_strategy
            )

            working_df, column_report = self._apply_strategy(
                working_df,
                column_name=column_name,
                strategy=strategy,
                dataset_name=dataset_name,
                config=config,
                missing_before=missing_before,
            )
            column_reports[str(column_name)] = column_report

        rows_after = working_df.shape[0]

        report = {
            "dataset_name": dataset_name,
            "rows_before": rows_before,
            "rows_after": rows_after,
            "rows_dropped_total": rows_before - rows_after,
            "columns_processed": len(column_reports),
            "columns": column_reports,
        }

        self._logger.log_operation(
            dataset_name=dataset_name,
            module=MODULE_NAME,
            operation="handle_missing_values",
            message=(
                f"Processed {len(column_reports)} column(s) with missing "
                f"values; rows {rows_before} -> {rows_after}."
            ),
            details={
                "rows_before": rows_before,
                "rows_after": rows_after,
                "columns_processed": len(column_reports),
            },
        )

        return working_df, report

    # ------------------------------------------------------------------ #
    # Strategy dispatch
    # ------------------------------------------------------------------ #

    def _apply_strategy(
        self,
        dataframe: pd.DataFrame,
        *,
        column_name: Any,
        strategy: MissingValueStrategy,
        dataset_name: str,
        config: MissingValueHandlingConfig,
        missing_before: int,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Apply a single remediation strategy to one column.

        Args:
            dataframe: The dataset being remediated (already a working copy).
            column_name: The column to remediate.
            strategy: The strategy to apply.
            dataset_name: Dataset identifier, for logging.
            config: The active configuration (used for business-rule
                default lookups).
            missing_before: The number of missing values in this column
                prior to remediation.

        Returns:
            A tuple of ``(possibly-modified dataframe, column_report)``.
        """
        dispatch = {
            MissingValueStrategy.DROP: self._apply_drop,
            MissingValueStrategy.MEAN: self._apply_mean,
            MissingValueStrategy.MEDIAN: self._apply_median,
            MissingValueStrategy.MODE: self._apply_mode,
            MissingValueStrategy.FORWARD_FILL: self._apply_forward_fill,
            MissingValueStrategy.BUSINESS_RULE: self._apply_business_rule,
            MissingValueStrategy.SKIP: self._apply_skip,
        }

        handler = dispatch.get(strategy, self._apply_skip)
        dataframe, fill_summary = handler(dataframe, column_name, config)

        missing_after = int(dataframe[column_name].isna().sum())
        rows_after = dataframe.shape[0]

        column_report = {
            "strategy_applied": strategy.value,
            "missing_before": missing_before,
            "missing_after": missing_after,
            "values_filled": max(missing_before - missing_after, 0)
            if strategy is not MissingValueStrategy.DROP
            else 0,
            "rows_dropped": fill_summary.get("rows_dropped", 0),
            "note": fill_summary.get("note"),
        }

        self._logger.log_operation(
            dataset_name=dataset_name,
            module=MODULE_NAME,
            operation=f"strategy_{strategy.value.lower()}",
            message=(
                f"Column '{column_name}': strategy={strategy.value}, "
                f"missing {missing_before} -> {missing_after}."
            ),
            details={"column": str(column_name), **column_report},
        )

        return dataframe, column_report

    @staticmethod
    def _apply_drop(
        dataframe: pd.DataFrame, column_name: Any, _config: MissingValueHandlingConfig
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Drop every row where ``column_name`` is missing."""
        rows_before = dataframe.shape[0]
        dataframe = dataframe.dropna(subset=[column_name])
        rows_dropped = rows_before - dataframe.shape[0]
        return dataframe, {"rows_dropped": rows_dropped}

    @staticmethod
    def _apply_mean(
        dataframe: pd.DataFrame, column_name: Any, _config: MissingValueHandlingConfig
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Fill missing values with the column mean (numeric columns only)."""
        series = dataframe[column_name]
        if not pd.api.types.is_numeric_dtype(series):
            return dataframe, {
                "note": "Mean strategy skipped: column is not numeric."
            }
        mean_value = series.mean()
        dataframe[column_name] = series.fillna(mean_value)
        return dataframe, {"note": f"Filled with mean value {mean_value!r}."}

    @staticmethod
    def _apply_median(
        dataframe: pd.DataFrame, column_name: Any, _config: MissingValueHandlingConfig
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Fill missing values with the column median (numeric columns only)."""
        series = dataframe[column_name]
        if not pd.api.types.is_numeric_dtype(series):
            return dataframe, {
                "note": "Median strategy skipped: column is not numeric."
            }
        median_value = series.median()
        dataframe[column_name] = series.fillna(median_value)
        return dataframe, {"note": f"Filled with median value {median_value!r}."}

    @staticmethod
    def _apply_mode(
        dataframe: pd.DataFrame, column_name: Any, _config: MissingValueHandlingConfig
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Fill missing values with the column's most frequent value."""
        series = dataframe[column_name]
        mode_values = series.mode(dropna=True)
        if mode_values.empty:
            return dataframe, {
                "note": "Mode strategy skipped: column has no non-null values."
            }
        mode_value = mode_values.iloc[0]
        dataframe[column_name] = series.fillna(mode_value)
        return dataframe, {"note": f"Filled with mode value {mode_value!r}."}

    @staticmethod
    def _apply_forward_fill(
        dataframe: pd.DataFrame, column_name: Any, _config: MissingValueHandlingConfig
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Forward-fill missing values from the preceding valid value."""
        dataframe[column_name] = dataframe[column_name].ffill()
        remaining = int(dataframe[column_name].isna().sum())
        note = "Forward-filled from preceding valid values."
        if remaining:
            note += (
                f" {remaining} value(s) remain missing (no prior value "
                "available, e.g. leading rows)."
            )
        return dataframe, {"note": note}

    @staticmethod
    def _apply_business_rule(
        dataframe: pd.DataFrame, column_name: Any, config: MissingValueHandlingConfig
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Fill missing values with a caller-supplied business-rule default."""
        if column_name not in config.business_rule_defaults:
            return dataframe, {
                "note": (
                    "Business rule strategy skipped: no default value "
                    "configured for this column."
                )
            }
        default_value = config.business_rule_defaults[column_name]
        dataframe[column_name] = dataframe[column_name].fillna(default_value)
        return dataframe, {"note": f"Filled with business-rule default {default_value!r}."}

    @staticmethod
    def _apply_skip(
        dataframe: pd.DataFrame, _column_name: Any, _config: MissingValueHandlingConfig
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Leave missing values untouched."""
        return dataframe, {"note": "Skipped: no remediation applied."}

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_percentage(numerator: int, denominator: int) -> float:
        """Compute a percentage while guarding against division by zero."""
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)


__all__ = ["MissingValueHandler", "MissingValueHandlingConfig", "MissingValueStrategy"]
