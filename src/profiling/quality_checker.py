"""Data quality scoring and warning engine.

Provides :class:`DataQualityChecker`, which consumes either a raw
``pandas.DataFrame`` or an existing profile produced by
:class:`src.profiling.data_profiler.DataProfiler` and derives an actionable
quality report: four dimension scores (completeness, uniqueness,
consistency, validity), a weighted overall score (0-100), and a list of
structured warnings flagging specific data quality issues.

Typical usage:
    >>> from src.profiling.quality_checker import DataQualityChecker
    >>> checker = DataQualityChecker()
    >>> report = checker.check_quality(df, dataset_name="vendors")
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd

from src.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Weights applied to each quality dimension when computing the overall
#: score. Must sum to 1.0.
DIMENSION_WEIGHTS: dict[str, float] = {
    "completeness": 0.30,
    "uniqueness": 0.25,
    "consistency": 0.25,
    "validity": 0.20,
}

#: A column's missing percentage above this threshold triggers a
#: HIGH_MISSING_VALUES warning.
HIGH_MISSING_VALUE_THRESHOLD_PCT: float = 30.0

#: A column's unique-value ratio above this threshold (for non-key-like,
#: high-cardinality object columns) triggers a HIGH_CARDINALITY warning.
HIGH_CARDINALITY_THRESHOLD_PCT: float = 90.0

#: Minimum number of rows required before cardinality warnings are
#: considered meaningful (avoids false positives on tiny datasets).
MIN_ROWS_FOR_CARDINALITY_CHECK: int = 20

#: Duplicate row percentage above this threshold triggers a
#: DUPLICATE_RECORDS warning.
DUPLICATE_ROW_THRESHOLD_PCT: float = 5.0

#: A column where every value is missing triggers an EMPTY_COLUMN warning.
EMPTY_COLUMN_MISSING_PCT: float = 100.0

#: Column name substrings (case-insensitive) that hint the column should
#: hold non-negative numeric values; used by the validity dimension.
NON_NEGATIVE_NAME_HINTS: tuple[str, ...] = (
    "price",
    "cost",
    "amount",
    "quantity",
    "qty",
    "value",
    "total",
    "spend",
)


class WarningSeverity(str, Enum):
    """Severity levels for quality warnings."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WarningType(str, Enum):
    """Enumerated categories of data quality warnings."""

    HIGH_MISSING_VALUES = "HIGH_MISSING_VALUES"
    DUPLICATE_RECORDS = "DUPLICATE_RECORDS"
    SUSPICIOUS_COLUMN = "SUSPICIOUS_COLUMN"
    HIGH_CARDINALITY = "HIGH_CARDINALITY"
    EMPTY_COLUMN = "EMPTY_COLUMN"
    INVALID_DATATYPE = "INVALID_DATATYPE"
    NEGATIVE_VALUES = "NEGATIVE_VALUES"


@dataclass
class QualityWarning:
    """A single structured data quality warning.

    Attributes:
        warning_type: The category of issue, from :class:`WarningType`.
        severity: The severity level, from :class:`WarningSeverity`.
        column: The affected column name, or ``None`` for dataset-level
            warnings.
        message: A human-readable description of the issue.
        details: Supplementary structured data about the issue.
    """

    warning_type: WarningType
    severity: WarningSeverity
    column: str | None
    message: str
    details: dict[str, Any]


class DataQualityChecker:
    """Compute quality scores and structured warnings for a dataset.

    The checker operates directly on a ``pandas.DataFrame`` so it can be
    used independently of :class:`src.profiling.data_profiler.DataProfiler`,
    while remaining fully compatible for pipelines that run both in
    sequence.
    """

    def __init__(self) -> None:
        """Initialize the quality checker."""
        logger.debug("DataQualityChecker initialized.")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def check_quality(
        self, dataframe: pd.DataFrame, *, dataset_name: str = "unnamed_dataset"
    ) -> dict[str, Any]:
        """Compute the full quality report for a dataset.

        Args:
            dataframe: The dataset to evaluate.
            dataset_name: A human-readable identifier for the dataset, used
                for logging and embedded in the output for traceability.

        Returns:
            A JSON-serializable dictionary containing:
                * ``dataset_name``: the provided identifier.
                * ``scores``: completeness, uniqueness, consistency,
                  validity, and overall_score (0-100).
                * ``warnings``: a list of structured warning dictionaries.
        """
        logger.info(
            "Running quality checks on dataset '%s' with shape=%s.",
            dataset_name,
            dataframe.shape,
        )

        completeness_score = self._score_completeness(dataframe)
        uniqueness_score = self._score_uniqueness(dataframe)
        consistency_score = self._score_consistency(dataframe)
        validity_score = self._score_validity(dataframe)

        overall_score = self._compute_overall_score(
            completeness_score, uniqueness_score, consistency_score, validity_score
        )

        warnings = self._collect_warnings(dataframe)

        report = {
            "dataset_name": dataset_name,
            "scores": {
                "completeness_score": completeness_score,
                "uniqueness_score": uniqueness_score,
                "consistency_score": consistency_score,
                "validity_score": validity_score,
                "overall_score": overall_score,
            },
            "warnings": [self._warning_to_dict(w) for w in warnings],
            "warning_count": len(warnings),
        }

        logger.info(
            "Quality check complete for '%s': overall_score=%.2f, warnings=%d.",
            dataset_name,
            overall_score,
            len(warnings),
        )
        return report

    # ------------------------------------------------------------------ #
    # Dimension scores
    # ------------------------------------------------------------------ #

    def _score_completeness(self, dataframe: pd.DataFrame) -> float:
        """Score the dataset on completeness (inverse of missingness).

        Args:
            dataframe: The dataset to evaluate.

        Returns:
            A score from 0-100 where 100 means no missing values anywhere.
        """
        total_cells = dataframe.size
        if total_cells == 0:
            return 0.0
        missing_cells = int(dataframe.isna().sum().sum())
        return round((1 - (missing_cells / total_cells)) * 100, 2)

    def _score_uniqueness(self, dataframe: pd.DataFrame) -> float:
        """Score the dataset on uniqueness (inverse of row duplication).

        Args:
            dataframe: The dataset to evaluate.

        Returns:
            A score from 0-100 where 100 means no duplicate rows.
        """
        row_count = dataframe.shape[0]
        if row_count == 0:
            return 0.0
        duplicate_count = int(dataframe.duplicated().sum())
        return round((1 - (duplicate_count / row_count)) * 100, 2)

    def _score_consistency(self, dataframe: pd.DataFrame) -> float:
        """Score the dataset on consistency of column data types.

        A column is considered consistent when, for object-dtype columns,
        every non-null value shares the same inferred Python type (e.g. not
        a mix of strings and numbers stored as objects).

        Args:
            dataframe: The dataset to evaluate.

        Returns:
            A score from 0-100 representing the proportion of columns with
            consistent typing.
        """
        if dataframe.shape[1] == 0:
            return 0.0

        consistent_columns = 0
        for column_name in dataframe.columns:
            series = dataframe[column_name].dropna()
            if series.empty:
                consistent_columns += 1
                continue
            if series.dtype != object:
                consistent_columns += 1
                continue
            inferred_types = series.map(type).nunique()
            if inferred_types <= 1:
                consistent_columns += 1

        return round((consistent_columns / dataframe.shape[1]) * 100, 2)

    def _score_validity(self, dataframe: pd.DataFrame) -> float:
        """Score the dataset on validity of values against basic domain rules.

        Currently checks numeric columns whose names suggest non-negative
        semantics (price, cost, quantity, etc.) for negative values.

        Args:
            dataframe: The dataset to evaluate.

        Returns:
            A score from 0-100 representing the proportion of checked cells
            that pass validity rules. Columns with no applicable rule are
            treated as fully valid.
        """
        total_checked = 0
        total_invalid = 0

        for column_name in dataframe.columns:
            if not self._matches_non_negative_hint(str(column_name)):
                continue
            series = dataframe[column_name]
            if not pd.api.types.is_numeric_dtype(series):
                continue
            clean = series.dropna()
            total_checked += len(clean)
            total_invalid += int((clean < 0).sum())

        if total_checked == 0:
            return 100.0

        return round((1 - (total_invalid / total_checked)) * 100, 2)

    def _compute_overall_score(
        self,
        completeness_score: float,
        uniqueness_score: float,
        consistency_score: float,
        validity_score: float,
    ) -> float:
        """Combine dimension scores into a single weighted overall score.

        Args:
            completeness_score: Score from :meth:`_score_completeness`.
            uniqueness_score: Score from :meth:`_score_uniqueness`.
            consistency_score: Score from :meth:`_score_consistency`.
            validity_score: Score from :meth:`_score_validity`.

        Returns:
            The weighted overall score, 0-100, rounded to two decimals.
        """
        overall = (
            completeness_score * DIMENSION_WEIGHTS["completeness"]
            + uniqueness_score * DIMENSION_WEIGHTS["uniqueness"]
            + consistency_score * DIMENSION_WEIGHTS["consistency"]
            + validity_score * DIMENSION_WEIGHTS["validity"]
        )
        return round(overall, 2)

    # ------------------------------------------------------------------ #
    # Warnings
    # ------------------------------------------------------------------ #

    def _collect_warnings(self, dataframe: pd.DataFrame) -> list[QualityWarning]:
        """Run every warning-detection rule against the dataset.

        Args:
            dataframe: The dataset to evaluate.

        Returns:
            A combined, ordered list of all triggered warnings.
        """
        warnings: list[QualityWarning] = []
        warnings.extend(self._check_duplicate_records(dataframe))
        for column_name in dataframe.columns:
            series = dataframe[column_name]
            warnings.extend(self._check_high_missing_values(column_name, series))
            warnings.extend(self._check_empty_column(column_name, series))
            warnings.extend(
                self._check_high_cardinality(column_name, series, dataframe.shape[0])
            )
            warnings.extend(self._check_suspicious_column(column_name, series))
            warnings.extend(self._check_invalid_datatype(column_name, series))
            warnings.extend(self._check_negative_values(column_name, series))
        return warnings

    def _check_duplicate_records(
        self, dataframe: pd.DataFrame
    ) -> list[QualityWarning]:
        """Flag the dataset when duplicate rows exceed the allowed threshold."""
        row_count = dataframe.shape[0]
        if row_count == 0:
            return []

        duplicate_count = int(dataframe.duplicated().sum())
        duplicate_pct = round((duplicate_count / row_count) * 100, 2)

        if duplicate_pct <= DUPLICATE_ROW_THRESHOLD_PCT or duplicate_count == 0:
            return []

        return [
            QualityWarning(
                warning_type=WarningType.DUPLICATE_RECORDS,
                severity=WarningSeverity.HIGH,
                column=None,
                message=(
                    f"{duplicate_count} duplicate rows detected "
                    f"({duplicate_pct}% of dataset)."
                ),
                details={
                    "duplicate_count": duplicate_count,
                    "duplicate_percentage": duplicate_pct,
                },
            )
        ]

    def _check_high_missing_values(
        self, column_name: Any, series: pd.Series
    ) -> list[QualityWarning]:
        """Flag columns whose missing-value percentage exceeds the threshold."""
        row_count = len(series)
        if row_count == 0:
            return []

        missing_count = int(series.isna().sum())
        missing_pct = round((missing_count / row_count) * 100, 2)

        if missing_pct <= HIGH_MISSING_VALUE_THRESHOLD_PCT or missing_pct >= 100:
            return []

        return [
            QualityWarning(
                warning_type=WarningType.HIGH_MISSING_VALUES,
                severity=WarningSeverity.MEDIUM,
                column=str(column_name),
                message=(
                    f"Column '{column_name}' has {missing_pct}% missing values."
                ),
                details={"missing_count": missing_count, "missing_percentage": missing_pct},
            )
        ]

    def _check_empty_column(
        self, column_name: Any, series: pd.Series
    ) -> list[QualityWarning]:
        """Flag columns that are entirely missing/empty."""
        row_count = len(series)
        if row_count == 0:
            return []

        missing_pct = round((series.isna().sum() / row_count) * 100, 2)
        if missing_pct < EMPTY_COLUMN_MISSING_PCT:
            return []

        return [
            QualityWarning(
                warning_type=WarningType.EMPTY_COLUMN,
                severity=WarningSeverity.CRITICAL,
                column=str(column_name),
                message=f"Column '{column_name}' contains no data (100% missing).",
                details={"missing_percentage": missing_pct},
            )
        ]

    def _check_high_cardinality(
        self, column_name: Any, series: pd.Series, row_count: int
    ) -> list[QualityWarning]:
        """Flag object/categorical columns with near-unique values.

        Skipped for small datasets and for columns already dtype-numeric,
        since high uniqueness there is often expected (e.g. measurements).
        """
        if row_count < MIN_ROWS_FOR_CARDINALITY_CHECK:
            return []
        if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(
            series
        ):
            return []

        non_null_count = int(series.notna().sum())
        if non_null_count == 0:
            return []

        unique_count = int(series.nunique(dropna=True))
        unique_pct = round((unique_count / non_null_count) * 100, 2)

        if unique_pct < HIGH_CARDINALITY_THRESHOLD_PCT:
            return []

        return [
            QualityWarning(
                warning_type=WarningType.HIGH_CARDINALITY,
                severity=WarningSeverity.LOW,
                column=str(column_name),
                message=(
                    f"Column '{column_name}' has high cardinality "
                    f"({unique_pct}% unique values); consider whether it "
                    "is an identifier rather than a categorical field."
                ),
                details={"unique_count": unique_count, "unique_percentage": unique_pct},
            )
        ]

    def _check_suspicious_column(
        self, column_name: Any, series: pd.Series
    ) -> list[QualityWarning]:
        """Flag columns with a single constant value across all rows.

        A column with zero variance rarely carries analytical value and
        often indicates a data extraction or upstream pipeline issue.
        """
        non_null = series.dropna()
        if non_null.empty:
            return []

        if non_null.nunique() != 1:
            return []

        return [
            QualityWarning(
                warning_type=WarningType.SUSPICIOUS_COLUMN,
                severity=WarningSeverity.LOW,
                column=str(column_name),
                message=(
                    f"Column '{column_name}' contains a single constant "
                    "value across all non-null rows."
                ),
                details={"constant_value": str(non_null.iloc[0])},
            )
        ]

    def _check_invalid_datatype(
        self, column_name: Any, series: pd.Series
    ) -> list[QualityWarning]:
        """Flag object columns that contain a mix of incompatible Python types."""
        non_null = series.dropna()
        if non_null.empty or non_null.dtype != object:
            return []

        inferred_types = non_null.map(type).unique()
        if len(inferred_types) <= 1:
            return []

        return [
            QualityWarning(
                warning_type=WarningType.INVALID_DATATYPE,
                severity=WarningSeverity.MEDIUM,
                column=str(column_name),
                message=(
                    f"Column '{column_name}' mixes multiple data types: "
                    f"{[t.__name__ for t in inferred_types]}."
                ),
                details={"types_found": [t.__name__ for t in inferred_types]},
            )
        ]

    def _check_negative_values(
        self, column_name: Any, series: pd.Series
    ) -> list[QualityWarning]:
        """Flag numeric columns whose name implies non-negativity but contain negatives."""
        if not self._matches_non_negative_hint(str(column_name)):
            return []
        if not pd.api.types.is_numeric_dtype(series):
            return []

        clean = series.dropna()
        negative_count = int((clean < 0).sum())
        if negative_count == 0:
            return []

        return [
            QualityWarning(
                warning_type=WarningType.NEGATIVE_VALUES,
                severity=WarningSeverity.MEDIUM,
                column=str(column_name),
                message=(
                    f"Column '{column_name}' contains {negative_count} "
                    "negative value(s) despite its name implying a "
                    "non-negative quantity."
                ),
                details={"negative_count": negative_count},
            )
        ]

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _matches_non_negative_hint(column_name: str) -> bool:
        """Check whether a column name suggests non-negative numeric semantics.

        Args:
            column_name: The column name to inspect.

        Returns:
            ``True`` if the lowercased name contains any hint substring from
            :data:`NON_NEGATIVE_NAME_HINTS`.
        """
        lowered = column_name.lower()
        return any(hint in lowered for hint in NON_NEGATIVE_NAME_HINTS)

    @staticmethod
    def _warning_to_dict(warning: QualityWarning) -> dict[str, Any]:
        """Convert a :class:`QualityWarning` into a JSON-serializable dict.

        Args:
            warning: The warning to convert.

        Returns:
            A dictionary representation with enum values expanded to strings.
        """
        return {
            "warning_type": warning.warning_type.value,
            "severity": warning.severity.value,
            "column": warning.column,
            "message": warning.message,
            "details": warning.details,
        }


__all__ = [
    "DataQualityChecker",
    "QualityWarning",
    "WarningType",
    "WarningSeverity",
]