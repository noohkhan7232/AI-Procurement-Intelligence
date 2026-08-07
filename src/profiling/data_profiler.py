"""Automated data profiling engine.

Provides :class:`DataProfiler`, a reusable, plugin-friendly engine that
computes structural and statistical profiles of any ``pandas.DataFrame``:
shape, dtypes, missingness, duplicates, memory footprint, per-column
statistics (numeric/categorical/date/boolean), and heuristic primary/foreign
key candidate detection.

The profiler is intentionally decoupled from I/O: it accepts an in-memory
``DataFrame`` and returns plain, JSON-serializable dictionaries, making it
trivially reusable across CLI tools, notebooks-free pipelines, APIs, and the
report generator in :mod:`src.profiling.generate_report`.

Typical usage:
    >>> from src.profiling.data_profiler import DataProfiler
    >>> profiler = DataProfiler()
    >>> profile = profiler.profile_dataframe(df, dataset_name="vendors")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np
import pandas as pd

from src.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: A column is considered a primary-key candidate when its uniqueness ratio
#: (unique values / non-null rows) meets or exceeds this threshold and it
#: has zero missing values.
PRIMARY_KEY_UNIQUENESS_THRESHOLD: float = 1.0

#: A column is considered a foreign-key candidate when its uniqueness ratio
#: falls within this (lower, upper) bound — repeated-but-varied values,
#: as opposed to a near-constant column or a fully-unique column.
FOREIGN_KEY_UNIQUENESS_LOWER_BOUND: float = 0.01
FOREIGN_KEY_UNIQUENESS_UPPER_BOUND: float = 0.95

#: Column name substrings that raise the heuristic likelihood of a column
#: being a key column, checked case-insensitively.
KEY_NAME_HINTS: tuple[str, ...] = ("id", "key", "code", "no", "number", "ref")

#: Number of top frequent values retained per categorical column summary.
TOP_CATEGORY_VALUES_COUNT: int = 10

#: Bytes-to-megabytes conversion factor.
BYTES_PER_MEGABYTE: float = 1024 * 1024


# --------------------------------------------------------------------------- #
# Plugin protocol (future extensibility)
# --------------------------------------------------------------------------- #


class ProfilerPlugin(Protocol):
    """Structural interface for future profiling plugins.

    Any callable/object implementing ``analyze(dataframe) -> dict`` can be
    registered with :meth:`DataProfiler.register_plugin` to extend the
    profiling output without modifying :class:`DataProfiler` itself,
    fulfilling the Open/Closed Principle.
    """

    def analyze(self, dataframe: pd.DataFrame) -> dict[str, Any]:
        """Analyze a dataframe and return a JSON-serializable result dict."""
        ...


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #


@dataclass
class ColumnProfile:
    """Structured profile for a single column.

    Attributes:
        name: Column name.
        dtype: Pandas dtype as a string.
        missing_count: Number of missing (NaN/None/NaT) values.
        missing_percentage: Percentage of rows that are missing, 0-100.
        unique_count: Number of distinct non-null values.
        unique_percentage: Percentage of non-null rows that are unique.
        semantic_type: Inferred semantic category: 'numeric', 'categorical',
            'datetime', or 'boolean'.
        statistics: Type-specific statistics (varies by ``semantic_type``).
    """

    name: str
    dtype: str
    missing_count: int
    missing_percentage: float
    unique_count: int
    unique_percentage: float
    semantic_type: str
    statistics: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Data profiler
# --------------------------------------------------------------------------- #


class DataProfiler:
    """Compute structural and statistical profiles of tabular datasets.

    The profiler is stateless with respect to data (each call to
    :meth:`profile_dataframe` is independent) but stateful with respect to
    registered plugins, allowing consumers to extend profiling behavior at
    runtime.
    """

    def __init__(self) -> None:
        """Initialize the profiler with an empty plugin registry."""
        self._plugins: dict[str, ProfilerPlugin] = {}
        logger.debug("DataProfiler initialized.")

    # ------------------------------------------------------------------ #
    # Plugin registry
    # ------------------------------------------------------------------ #

    def register_plugin(self, name: str, plugin: ProfilerPlugin) -> None:
        """Register a plugin that extends the profiling output.

        Args:
            name: Unique key under which the plugin's result will be stored
                in the ``plugins`` section of the profile output.
            plugin: An object implementing the :class:`ProfilerPlugin`
                protocol (i.e. an ``analyze(dataframe) -> dict`` method).
        """
        logger.info("Registering profiler plugin '%s'.", name)
        self._plugins[name] = plugin

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def profile_dataframe(
        self, dataframe: pd.DataFrame, *, dataset_name: str = "unnamed_dataset"
    ) -> dict[str, Any]:
        """Compute a full structural and statistical profile of a dataframe.

        Args:
            dataframe: The dataset to profile.
            dataset_name: A human-readable identifier for the dataset, used
                only for logging and embedded in the output for traceability.

        Returns:
            A JSON-serializable dictionary containing:
                * ``dataset_name``: the provided identifier.
                * ``overview``: row/column counts, memory usage, duplicates.
                * ``columns``: per-column profiles keyed by column name.
                * ``primary_key_candidates``: list of candidate column names.
                * ``foreign_key_candidates``: list of candidate column names.
                * ``plugins``: results from any registered plugins.
        """
        logger.info(
            "Profiling dataset '%s' with shape=%s.", dataset_name, dataframe.shape
        )

        overview = self._build_overview(dataframe)
        columns = self._build_column_profiles(dataframe)
        primary_key_candidates = self._detect_primary_key_candidates(
            dataframe, columns
        )
        foreign_key_candidates = self._detect_foreign_key_candidates(
            dataframe, columns, primary_key_candidates
        )
        plugin_results = self._run_plugins(dataframe)

        profile = {
            "dataset_name": dataset_name,
            "overview": overview,
            "columns": {name: vars(col) for name, col in columns.items()},
            "primary_key_candidates": primary_key_candidates,
            "foreign_key_candidates": foreign_key_candidates,
            "plugins": plugin_results,
        }

        logger.info("Completed profiling for dataset '%s'.", dataset_name)
        return profile

    # ------------------------------------------------------------------ #
    # Overview
    # ------------------------------------------------------------------ #

    def _build_overview(self, dataframe: pd.DataFrame) -> dict[str, Any]:
        """Compute dataset-level (non-column-specific) metrics.

        Args:
            dataframe: The dataset to summarize.

        Returns:
            A dictionary of dataset-level metrics.
        """
        row_count = int(dataframe.shape[0])
        column_count = int(dataframe.shape[1])
        duplicate_row_count = int(dataframe.duplicated().sum())
        memory_bytes = int(dataframe.memory_usage(deep=True).sum())

        return {
            "row_count": row_count,
            "column_count": column_count,
            "column_names": list(dataframe.columns.astype(str)),
            "duplicate_row_count": duplicate_row_count,
            "duplicate_row_percentage": self._safe_percentage(
                duplicate_row_count, row_count
            ),
            "memory_usage_bytes": memory_bytes,
            "memory_usage_mb": round(memory_bytes / BYTES_PER_MEGABYTE, 4),
        }

    # ------------------------------------------------------------------ #
    # Column profiling
    # ------------------------------------------------------------------ #

    def _build_column_profiles(
        self, dataframe: pd.DataFrame
    ) -> dict[str, ColumnProfile]:
        """Build a :class:`ColumnProfile` for every column in the dataframe.

        Args:
            dataframe: The dataset whose columns will be profiled.

        Returns:
            A mapping of column name to its :class:`ColumnProfile`.
        """
        row_count = dataframe.shape[0]
        profiles: dict[str, ColumnProfile] = {}

        for column_name in dataframe.columns:
            series = dataframe[column_name]
            missing_count = int(series.isna().sum())
            non_null_count = int(row_count - missing_count)
            unique_count = int(series.nunique(dropna=True))
            semantic_type = self._infer_semantic_type(series)

            profile = ColumnProfile(
                name=str(column_name),
                dtype=str(series.dtype),
                missing_count=missing_count,
                missing_percentage=self._safe_percentage(missing_count, row_count),
                unique_count=unique_count,
                unique_percentage=self._safe_percentage(
                    unique_count, non_null_count
                ),
                semantic_type=semantic_type,
                statistics=self._compute_type_statistics(series, semantic_type),
            )
            profiles[str(column_name)] = profile

        return profiles

    @staticmethod
    def _infer_semantic_type(series: pd.Series) -> str:
        """Infer a high-level semantic type for a column.

        Args:
            series: The column to classify.

        Returns:
            One of ``'boolean'``, ``'datetime'``, ``'numeric'``, or
            ``'categorical'``.
        """
        if pd.api.types.is_bool_dtype(series):
            return "boolean"
        if pd.api.types.is_datetime64_any_dtype(series):
            return "datetime"
        if pd.api.types.is_numeric_dtype(series):
            return "numeric"
        return "categorical"

    def _compute_type_statistics(
        self, series: pd.Series, semantic_type: str
    ) -> dict[str, Any]:
        """Dispatch to the appropriate type-specific statistics computation.

        Args:
            series: The column to analyze.
            semantic_type: The semantic type returned by
                :meth:`_infer_semantic_type`.

        Returns:
            A dictionary of statistics relevant to the column's type.
        """
        dispatch = {
            "numeric": self._compute_numeric_statistics,
            "categorical": self._compute_categorical_statistics,
            "datetime": self._compute_datetime_statistics,
            "boolean": self._compute_boolean_statistics,
        }
        handler = dispatch.get(semantic_type, self._compute_categorical_statistics)
        try:
            return handler(series)
        except Exception:  # noqa: BLE001 - profiling must never crash the pipeline
            logger.exception(
                "Failed computing '%s' statistics for column '%s'; "
                "returning empty statistics.",
                semantic_type,
                series.name,
            )
            return {}

    @staticmethod
    def _compute_numeric_statistics(series: pd.Series) -> dict[str, Any]:
        """Compute summary statistics for a numeric column.

        Args:
            series: A numeric column.

        Returns:
            A dictionary with min, max, mean, median, std, quartiles, and
            zero/negative value counts.
        """
        clean = series.dropna()
        if clean.empty:
            return {}

        return {
            "min": float(clean.min()),
            "max": float(clean.max()),
            "mean": float(clean.mean()),
            "median": float(clean.median()),
            "std": float(clean.std()) if len(clean) > 1 else 0.0,
            "q25": float(clean.quantile(0.25)),
            "q75": float(clean.quantile(0.75)),
            "zero_count": int((clean == 0).sum()),
            "negative_count": int((clean < 0).sum()),
        }

    @staticmethod
    def _compute_categorical_statistics(series: pd.Series) -> dict[str, Any]:
        """Compute summary statistics for a categorical/text column.

        Args:
            series: A categorical or object-dtype column.

        Returns:
            A dictionary with the most frequent values and their counts,
            plus min/max string length.
        """
        clean = series.dropna().astype(str)
        if clean.empty:
            return {}

        value_counts = clean.value_counts().head(TOP_CATEGORY_VALUES_COUNT)
        lengths = clean.str.len()

        return {
            "top_values": {str(k): int(v) for k, v in value_counts.items()},
            "min_length": int(lengths.min()),
            "max_length": int(lengths.max()),
            "average_length": round(float(lengths.mean()), 2),
        }

    @staticmethod
    def _compute_datetime_statistics(series: pd.Series) -> dict[str, Any]:
        """Compute summary statistics for a datetime column.

        Args:
            series: A datetime64-dtype column.

        Returns:
            A dictionary with the min/max timestamp and the implied range
            in days.
        """
        clean = series.dropna()
        if clean.empty:
            return {}

        min_date = clean.min()
        max_date = clean.max()

        return {
            "min_date": min_date.isoformat(),
            "max_date": max_date.isoformat(),
            "range_days": int((max_date - min_date).days),
        }

    @staticmethod
    def _compute_boolean_statistics(series: pd.Series) -> dict[str, Any]:
        """Compute summary statistics for a boolean column.

        Args:
            series: A boolean-dtype column.

        Returns:
            A dictionary with true/false counts and percentages.
        """
        clean = series.dropna()
        total = len(clean)
        if total == 0:
            return {}

        true_count = int(clean.sum())
        false_count = int(total - true_count)

        return {
            "true_count": true_count,
            "false_count": false_count,
            "true_percentage": round((true_count / total) * 100, 2),
            "false_percentage": round((false_count / total) * 100, 2),
        }

    # ------------------------------------------------------------------ #
    # Key candidate detection
    # ------------------------------------------------------------------ #

    def _detect_primary_key_candidates(
        self, dataframe: pd.DataFrame, columns: dict[str, ColumnProfile]
    ) -> list[str]:
        """Heuristically detect columns likely to be primary keys.

        A column qualifies when it has zero missing values and a
        uniqueness ratio of :data:`PRIMARY_KEY_UNIQUENESS_THRESHOLD`
        (i.e. every value is distinct).

        Args:
            dataframe: The source dataset (used to confirm row count > 0).
            columns: Precomputed column profiles.

        Returns:
            A list of column names that are candidate primary keys.
        """
        if dataframe.empty:
            return []

        candidates = [
            name
            for name, profile in columns.items()
            if profile.missing_count == 0
            and profile.unique_percentage >= PRIMARY_KEY_UNIQUENESS_THRESHOLD * 100
        ]
        logger.debug("Primary key candidates detected: %s", candidates)
        return candidates

    def _detect_foreign_key_candidates(
        self,
        dataframe: pd.DataFrame,
        columns: dict[str, ColumnProfile],
        primary_key_candidates: list[str],
    ) -> list[str]:
        """Heuristically detect columns likely to be foreign keys.

        A column qualifies when its uniqueness ratio falls strictly between
        :data:`FOREIGN_KEY_UNIQUENESS_LOWER_BOUND` and
        :data:`FOREIGN_KEY_UNIQUENESS_UPPER_BOUND` (i.e. it repeats, but
        isn't a low-cardinality flag/category column), it is not already a
        primary-key candidate, and its name matches common key-naming
        conventions.

        Args:
            dataframe: The source dataset.
            columns: Precomputed column profiles.
            primary_key_candidates: Columns already classified as primary
                keys, excluded from foreign-key consideration.

        Returns:
            A list of column names that are candidate foreign keys.
        """
        if dataframe.empty:
            return []

        candidates: list[str] = []
        for name, profile in columns.items():
            if name in primary_key_candidates:
                continue

            uniqueness_ratio = profile.unique_percentage / 100
            within_bounds = (
                FOREIGN_KEY_UNIQUENESS_LOWER_BOUND
                < uniqueness_ratio
                < FOREIGN_KEY_UNIQUENESS_UPPER_BOUND
            )
            name_hints_key = any(
                hint in name.lower() for hint in KEY_NAME_HINTS
            )

            if within_bounds and name_hints_key:
                candidates.append(name)

        logger.debug("Foreign key candidates detected: %s", candidates)
        return candidates

    # ------------------------------------------------------------------ #
    # Plugins
    # ------------------------------------------------------------------ #

    def _run_plugins(self, dataframe: pd.DataFrame) -> dict[str, Any]:
        """Execute every registered plugin against the dataframe.

        Plugin failures are logged and isolated so a single misbehaving
        plugin cannot break the core profiling pipeline.

        Args:
            dataframe: The dataset to pass to each plugin.

        Returns:
            A mapping of plugin name to its analysis result (or an error
            payload if the plugin raised an exception).
        """
        results: dict[str, Any] = {}
        for name, plugin in self._plugins.items():
            try:
                logger.info("Running profiler plugin '%s'.", name)
                results[name] = plugin.analyze(dataframe)
            except Exception as exc:  # noqa: BLE001 - isolate plugin failures
                logger.exception("Plugin '%s' failed during analysis.", name)
                results[name] = {"error": str(exc)}
        return results

    # ------------------------------------------------------------------ #
    # Shared helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_percentage(numerator: int, denominator: int) -> float:
        """Compute a percentage while guarding against division by zero.

        Args:
            numerator: The count representing the subset.
            denominator: The count representing the whole.

        Returns:
            The percentage rounded to two decimal places, or ``0.0`` when
            ``denominator`` is zero.
        """
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)


__all__ = ["DataProfiler", "ColumnProfile", "ProfilerPlugin"]