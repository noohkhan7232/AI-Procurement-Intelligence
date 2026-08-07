"""Data type standardization and conversion validation.

Provides :class:`DataTypeStandardizer`, which converts DataFrame columns to
explicitly requested target types (string, integer, float, boolean,
datetime, categorical) and/or automatically infers sensible types for
columns the caller does not configure explicitly. Every conversion is
validated: values that fail to convert become nulls, and the number of
newly-introduced nulls is tracked and reported so silent data loss is never
invisible.

Typical usage:
    >>> from src.cleaning.datatype_standardizer import (
    ...     DataTypeStandardizer, DataTypeStandardizationConfig, TargetDataType,
    ... )
    >>> from src.utils.cleaning_logger import CleaningLogger
    >>> standardizer = DataTypeStandardizer(CleaningLogger(__name__))
    >>> config = DataTypeStandardizationConfig(
    ...     column_types={"price": TargetDataType.FLOAT, "is_active": TargetDataType.BOOLEAN},
    ... )
    >>> cleaned_df, report = standardizer.standardize(df, dataset_name="vendors", config=config)
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
MODULE_NAME: str = "DataTypeStandardizer"

#: String tokens (lowercased) recognized as boolean ``True`` during
#: automatic inference and explicit boolean conversion.
TRUE_TOKENS: frozenset[str] = frozenset({"true", "yes", "y", "1", "t"})

#: String tokens (lowercased) recognized as boolean ``False`` during
#: automatic inference and explicit boolean conversion.
FALSE_TOKENS: frozenset[str] = frozenset({"false", "no", "n", "0", "f"})

#: Minimum fraction of non-null values that must successfully parse as a
#: given target type for automatic inference to adopt that type.
AUTO_INFERENCE_SUCCESS_THRESHOLD: float = 0.90

#: Maximum ratio of unique values to non-null rows for an inferred object
#: column to be converted to ``category`` dtype during automatic inference.
AUTO_CATEGORICAL_UNIQUENESS_THRESHOLD: float = 0.50

#: Minimum number of non-null rows required before automatic categorical
#: inference is attempted (avoids converting trivially small columns).
AUTO_CATEGORICAL_MIN_ROWS: int = 10


class TargetDataType(str, Enum):
    """Supported explicit target types for column conversion."""

    STRING = "STRING"
    INTEGER = "INTEGER"
    FLOAT = "FLOAT"
    BOOLEAN = "BOOLEAN"
    DATETIME = "DATETIME"
    CATEGORICAL = "CATEGORICAL"


@dataclass
class DataTypeStandardizationConfig:
    """Configuration controlling data type standardization.

    Attributes:
        column_types: Explicit target type per column, keyed by column name.
            Takes precedence over automatic inference.
        auto_infer_remaining: Whether columns not listed in ``column_types``
            should have their type automatically inferred. Defaults to
            ``True``.
        datetime_format: An explicit ``strftime``-style format string used
            when parsing datetime columns. If ``None``, pandas' flexible
            parser is used instead.
    """

    column_types: dict[str, TargetDataType] = field(default_factory=dict)
    auto_infer_remaining: bool = True
    datetime_format: str | None = None


class DataTypeStandardizer:
    """Convert and validate DataFrame column data types.

    Single responsibility: type conversion and conversion validation only.
    Column *naming* is handled by :class:`~src.cleaning.column_standardizer.ColumnStandardizer`,
    and missing-value semantics by
    :class:`~src.cleaning.missing_value_handler.MissingValueHandler`.
    """

    def __init__(self, cleaning_logger: CleaningLogger) -> None:
        """Initialize the standardizer.

        Args:
            cleaning_logger: Shared structured logger used to record every
                conversion decision for later inclusion in reports.
        """
        self._logger = cleaning_logger

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def standardize(
        self,
        dataframe: pd.DataFrame,
        *,
        dataset_name: str,
        config: DataTypeStandardizationConfig | None = None,
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Standardize column data types according to the given configuration.

        Args:
            dataframe: The dataset to convert. Not modified in place; a
                converted copy is returned.
            dataset_name: Human-readable dataset identifier, used for
                logging and embedded in the returned report.
            config: The standardization configuration. If ``None``, defaults
                to automatic inference for every column.

        Returns:
            A tuple of ``(converted_dataframe, report)`` where ``report`` is
            a JSON-serializable dictionary describing every conversion
            attempted, including validation outcomes.
        """
        config = config or DataTypeStandardizationConfig()
        working_df = dataframe.copy()
        column_reports: dict[str, Any] = {}

        for column_name in list(working_df.columns):
            original_dtype = str(working_df[column_name].dtype)
            before_series = working_df[column_name]

            if column_name in config.column_types:
                target_type = config.column_types[column_name]
                working_df[column_name] = self._convert_column(
                    before_series, target_type, config
                )
                inference_used = False
            elif config.auto_infer_remaining:
                converted_series, target_type = self._infer_and_convert(before_series)
                working_df[column_name] = converted_series
                inference_used = True
            else:
                continue

            column_report = self._validate_conversion(
                before_series, working_df[column_name], original_dtype, target_type
            )
            column_report["inference_used"] = inference_used
            column_reports[str(column_name)] = column_report

            self._logger.log_operation(
                dataset_name=dataset_name,
                module=MODULE_NAME,
                operation="convert_column",
                message=(
                    f"Column '{column_name}': {original_dtype} -> "
                    f"{column_report['resulting_dtype']} "
                    f"(target={target_type.value}, "
                    f"failures={column_report['conversion_failures']})."
                ),
                details={"column": str(column_name), **column_report},
                level="warning" if column_report["conversion_failures"] > 0 else "info",
            )

        report = {
            "dataset_name": dataset_name,
            "columns_converted": len(column_reports),
            "columns": column_reports,
        }
        return working_df, report

    # ------------------------------------------------------------------ #
    # Explicit conversion
    # ------------------------------------------------------------------ #

    def _convert_column(
        self,
        series: pd.Series,
        target_type: TargetDataType,
        config: DataTypeStandardizationConfig,
    ) -> pd.Series:
        """Convert a single column to an explicitly requested target type.

        Args:
            series: The column to convert.
            target_type: The requested target type.
            config: The active standardization configuration (used for the
                optional explicit datetime format).

        Returns:
            The converted series. Values that fail to convert become null
            rather than raising, so the caller's row count is preserved.
        """
        dispatch = {
            TargetDataType.STRING: self._to_string,
            TargetDataType.INTEGER: self._to_integer,
            TargetDataType.FLOAT: self._to_float,
            TargetDataType.BOOLEAN: self._to_boolean,
            TargetDataType.DATETIME: lambda s: self._to_datetime(
                s, config.datetime_format
            ),
            TargetDataType.CATEGORICAL: self._to_categorical,
        }
        return dispatch[target_type](series)

    @staticmethod
    def _to_string(series: pd.Series) -> pd.Series:
        """Convert a column to nullable string dtype."""
        return series.astype("string")

    @staticmethod
    def _to_integer(series: pd.Series) -> pd.Series:
        """Convert a column to nullable integer dtype (Int64)."""
        numeric = pd.to_numeric(series, errors="coerce")
        return numeric.round().astype("Int64")

    @staticmethod
    def _to_float(series: pd.Series) -> pd.Series:
        """Convert a column to float64, coercing unparsable values to NaN."""
        return pd.to_numeric(series, errors="coerce").astype("float64")

    @staticmethod
    def _to_boolean(series: pd.Series) -> pd.Series:
        """Convert a column to nullable boolean dtype.

        Accepts native booleans, numeric 0/1, and common textual tokens
        (see :data:`TRUE_TOKENS` / :data:`FALSE_TOKENS`), case-insensitively.
        """

        def _coerce(value: Any) -> Any:
            if pd.isna(value):
                return pd.NA
            if isinstance(value, bool):
                return value
            if isinstance(value, (int, float)):
                if value == 1:
                    return True
                if value == 0:
                    return False
                return pd.NA
            text = str(value).strip().lower()
            if text in TRUE_TOKENS:
                return True
            if text in FALSE_TOKENS:
                return False
            return pd.NA

        return series.map(_coerce).astype("boolean")

    @staticmethod
    def _to_datetime(series: pd.Series, datetime_format: str | None) -> pd.Series:
        """Convert a column to datetime64, coercing unparsable values to NaT."""
        return pd.to_datetime(series, format=datetime_format, errors="coerce")

    @staticmethod
    def _to_categorical(series: pd.Series) -> pd.Series:
        """Convert a column to pandas ``category`` dtype."""
        return series.astype("category")

    # ------------------------------------------------------------------ #
    # Automatic inference
    # ------------------------------------------------------------------ #

    def _infer_and_convert(
        self, series: pd.Series
    ) -> tuple[pd.Series, TargetDataType]:
        """Infer the most appropriate target type for a column and convert it.

        Inference order: boolean -> integer -> float -> datetime ->
        categorical (for low-cardinality text) -> string (fallback). A
        candidate type is only adopted when at least
        :data:`AUTO_INFERENCE_SUCCESS_THRESHOLD` of non-null values parse
        successfully, so a column of genuinely mixed free text is left as
        string rather than being aggressively coerced.

        Args:
            series: The column to infer a type for.

        Returns:
            A tuple of ``(converted_series, chosen_target_type)``.
        """
        non_null = series.dropna()
        if non_null.empty:
            return series.astype("string"), TargetDataType.STRING

        if pd.api.types.is_bool_dtype(series):
            return series.astype("boolean"), TargetDataType.BOOLEAN

        if pd.api.types.is_numeric_dtype(series):
            if (non_null == non_null.round()).all():
                return self._to_integer(series), TargetDataType.INTEGER
            return self._to_float(series), TargetDataType.FLOAT

        if pd.api.types.is_datetime64_any_dtype(series):
            return series, TargetDataType.DATETIME

        boolean_attempt = self._to_boolean(series)
        if self._success_ratio(non_null, boolean_attempt) >= (
            AUTO_INFERENCE_SUCCESS_THRESHOLD
        ):
            return boolean_attempt, TargetDataType.BOOLEAN

        numeric_attempt = pd.to_numeric(series, errors="coerce")
        if self._success_ratio(non_null, numeric_attempt) >= (
            AUTO_INFERENCE_SUCCESS_THRESHOLD
        ):
            if (numeric_attempt.dropna() == numeric_attempt.dropna().round()).all():
                return numeric_attempt.astype("Int64"), TargetDataType.INTEGER
            return numeric_attempt.astype("float64"), TargetDataType.FLOAT

        datetime_attempt = pd.to_datetime(series, errors="coerce")
        if self._success_ratio(non_null, datetime_attempt) >= (
            AUTO_INFERENCE_SUCCESS_THRESHOLD
        ):
            return datetime_attempt, TargetDataType.DATETIME

        unique_ratio = non_null.nunique() / len(non_null)
        if (
            len(non_null) >= AUTO_CATEGORICAL_MIN_ROWS
            and unique_ratio <= AUTO_CATEGORICAL_UNIQUENESS_THRESHOLD
        ):
            return series.astype("category"), TargetDataType.CATEGORICAL

        return series.astype("string"), TargetDataType.STRING

    @staticmethod
    def _success_ratio(original_non_null: pd.Series, converted: pd.Series) -> float:
        """Compute the fraction of originally non-null values that converted.

        Args:
            original_non_null: The non-null subset of the original column.
            converted: The full converted column (same index as the
                original, pre-``dropna``, column).

        Returns:
            The fraction (0.0-1.0) of values that were non-null both before
            and after conversion.
        """
        if original_non_null.empty:
            return 0.0
        converted_at_original_index = converted.loc[original_non_null.index]
        successful = converted_at_original_index.notna().sum()
        return successful / len(original_non_null)

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_conversion(
        before: pd.Series,
        after: pd.Series,
        original_dtype: str,
        target_type: TargetDataType,
    ) -> dict[str, Any]:
        """Compare a column before/after conversion and summarize the outcome.

        A "conversion failure" is any value that was non-null before
        conversion but became null afterward — i.e. data that could not be
        represented in the target type and was silently coerced.

        Args:
            before: The column prior to conversion.
            after: The column after conversion.
            original_dtype: The dtype string prior to conversion.
            target_type: The type conversion that was attempted.

        Returns:
            A dictionary summarizing the conversion outcome.
        """
        was_non_null = before.notna()
        now_null = after.isna()
        conversion_failures = int((was_non_null & now_null).sum())
        eligible = int(was_non_null.sum())
        success_rate = (
            round(((eligible - conversion_failures) / eligible) * 100, 2)
            if eligible > 0
            else 100.0
        )

        return {
            "original_dtype": original_dtype,
            "target_dtype": target_type.value,
            "resulting_dtype": str(after.dtype),
            "conversion_failures": conversion_failures,
            "success_rate_percentage": success_rate,
        }


__all__ = [
    "DataTypeStandardizer",
    "DataTypeStandardizationConfig",
    "TargetDataType",
]
