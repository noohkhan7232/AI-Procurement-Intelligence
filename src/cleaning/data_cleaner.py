"""Single-dataset cleaning coordination.

Provides :class:`DataCleaner`, which orchestrates
:class:`~src.cleaning.column_standardizer.ColumnStandardizer`,
:class:`~src.cleaning.datatype_standardizer.DataTypeStandardizer`,
:class:`~src.cleaning.missing_value_handler.MissingValueHandler`,
:class:`~src.cleaning.duplicate_handler.DuplicateHandler`, and
:class:`~src.cleaning.business_rule_validator.BusinessRuleValidator` into a
single, deterministic cleaning sequence for one dataset, returning both the
cleaned DataFrame and complete cleaning metadata.

Pipeline order (chosen so each stage operates on data already normalized by
the previous one):

    1. Column name standardization — every later stage can then reference
       predictable, snake_case column names.
    2. Data type standardization — numeric/boolean/datetime coercion before
       missing-value statistics (e.g. mean/median) are computed on them.
    3. Missing value handling — remediate nulls using the now-correct dtypes.
    4. Duplicate removal — deduplicate the fully-typed, null-remediated data.
    5. Business rule validation — validate the final, cleaned dataset and
       report any remaining domain violations (validation does not mutate
       data; it surfaces issues for human review).

Typical usage:
    >>> from src.cleaning.data_cleaner import DataCleaner
    >>> cleaner = DataCleaner()
    >>> result = cleaner.clean(df, dataset_name="vendors")
    >>> result.cleaned_dataframe
    >>> result.metadata
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.cleaning.business_rule_validator import BusinessRuleValidator
from src.cleaning.column_standardizer import ColumnStandardizer
from src.cleaning.datatype_standardizer import (
    DataTypeStandardizationConfig,
    DataTypeStandardizer,
)
from src.cleaning.duplicate_handler import DuplicateHandler, DuplicateHandlingConfig
from src.cleaning.missing_value_handler import (
    MissingValueHandler,
    MissingValueHandlingConfig,
)
from src.utils.cleaning_logger import CleaningLogger

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Module identifier used in structured log entries.
MODULE_NAME: str = "DataCleaner"


@dataclass
class DataCleaningConfig:
    """Aggregate configuration for a single dataset cleaning run.

    Attributes:
        missing_value_config: Configuration for
            :class:`~src.cleaning.missing_value_handler.MissingValueHandler`.
        duplicate_config: Configuration for
            :class:`~src.cleaning.duplicate_handler.DuplicateHandler`.
        datatype_config: Configuration for
            :class:`~src.cleaning.datatype_standardizer.DataTypeStandardizer`.
        enable_column_standardization: Whether column names should be
            normalized to snake_case. Defaults to ``True``.
        enable_business_rules: Whether business rule validation should run.
            Defaults to ``True``.
        use_default_business_ruleset: When ``enable_business_rules`` is
            ``True`` and no rules have been explicitly registered on
            ``business_rule_validator``, whether to auto-populate a
            heuristic default ruleset based on the (post-standardization)
            column names. Defaults to ``True``.
        business_rule_validator: An optional, pre-configured validator with
            custom rules already registered. If ``None``, a fresh one is
            created per dataset.
    """

    missing_value_config: MissingValueHandlingConfig = field(
        default_factory=MissingValueHandlingConfig
    )
    duplicate_config: DuplicateHandlingConfig = field(
        default_factory=DuplicateHandlingConfig
    )
    datatype_config: DataTypeStandardizationConfig = field(
        default_factory=DataTypeStandardizationConfig
    )
    enable_column_standardization: bool = True
    enable_business_rules: bool = True
    use_default_business_ruleset: bool = True
    business_rule_validator: BusinessRuleValidator | None = None


@dataclass
class CleaningResult:
    """The complete outcome of cleaning a single dataset.

    Attributes:
        dataset_name: The dataset identifier.
        cleaned_dataframe: The fully cleaned DataFrame.
        rows_before: Row count prior to cleaning.
        rows_after: Row count after cleaning.
        columns_before: Column count prior to cleaning.
        columns_after: Column count after cleaning.
        duration_seconds: Wall-clock time spent cleaning this dataset.
        column_report: Output of
            :meth:`~src.cleaning.column_standardizer.ColumnStandardizer.standardize`.
        datatype_report: Output of
            :meth:`~src.cleaning.datatype_standardizer.DataTypeStandardizer.standardize`.
        missing_value_report: Output of
            :meth:`~src.cleaning.missing_value_handler.MissingValueHandler.handle`.
        duplicate_report: Output of
            :meth:`~src.cleaning.duplicate_handler.DuplicateHandler.remove`.
        validation_report: Output of
            :meth:`~src.cleaning.business_rule_validator.BusinessRuleValidator.validate`,
            or ``None`` if business rule validation was disabled.
    """

    dataset_name: str
    cleaned_dataframe: pd.DataFrame
    rows_before: int
    rows_after: int
    columns_before: int
    columns_after: int
    duration_seconds: float
    column_report: dict[str, Any]
    datatype_report: dict[str, Any]
    missing_value_report: dict[str, Any]
    duplicate_report: dict[str, Any]
    validation_report: dict[str, Any] | None

    @property
    def metadata(self) -> dict[str, Any]:
        """Assemble every sub-report into a single JSON-serializable dict.

        Returns:
            A dictionary combining overview statistics with each stage's
            detailed report, suitable for direct persistence.
        """
        return {
            "dataset_name": self.dataset_name,
            "rows_before": self.rows_before,
            "rows_after": self.rows_after,
            "columns_before": self.columns_before,
            "columns_after": self.columns_after,
            "duration_seconds": round(self.duration_seconds, 4),
            "column_standardization": self.column_report,
            "datatype_standardization": self.datatype_report,
            "missing_value_handling": self.missing_value_report,
            "duplicate_handling": self.duplicate_report,
            "business_rule_validation": self.validation_report,
        }


class DataCleaner:
    """Coordinate every cleaning module for a single dataset.

    This class contains no cleaning logic of its own — it is a thin,
    ordered orchestrator over the five specialized modules, each of which
    remains fully independently testable and reusable outside this
    coordinator.
    """

    def __init__(self, cleaning_logger: CleaningLogger | None = None) -> None:
        """Initialize the coordinator and its sub-modules.

        Args:
            cleaning_logger: A shared structured logger. If ``None``, a new
                :class:`~src.utils.cleaning_logger.CleaningLogger` is
                created and shared across every sub-module constructed here.
        """
        self._logger = cleaning_logger or CleaningLogger(__name__)
        self._column_standardizer = ColumnStandardizer(self._logger)
        self._datatype_standardizer = DataTypeStandardizer(self._logger)
        self._missing_value_handler = MissingValueHandler(self._logger)
        self._duplicate_handler = DuplicateHandler(self._logger)

    @property
    def cleaning_logger(self) -> CleaningLogger:
        """The structured logger shared by every sub-module of this cleaner."""
        return self._logger

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def clean(
        self,
        dataframe: pd.DataFrame,
        *,
        dataset_name: str,
        config: DataCleaningConfig | None = None,
    ) -> CleaningResult:
        """Run the full cleaning sequence against a single dataset.

        Args:
            dataframe: The raw dataset to clean. Never modified in place.
            dataset_name: Human-readable dataset identifier, used for
                logging and embedded in every sub-report.
            config: The cleaning configuration. If ``None``, sensible
                defaults are used for every stage (mode-fill missing
                values, full-row duplicate removal keeping the first
                occurrence, automatic type inference, and a heuristic
                default business ruleset).

        Returns:
            A :class:`CleaningResult` containing the cleaned DataFrame and
            complete cleaning metadata.
        """
        config = config or DataCleaningConfig()
        started_at = time.perf_counter()

        rows_before = dataframe.shape[0]
        columns_before = dataframe.shape[1]

        working_df = dataframe

        if config.enable_column_standardization:
            working_df, column_report = self._column_standardizer.standardize(
                working_df, dataset_name=dataset_name
            )
        else:
            column_report = {"skipped": True}

        working_df, datatype_report = self._datatype_standardizer.standardize(
            working_df, dataset_name=dataset_name, config=config.datatype_config
        )

        working_df, missing_value_report = self._missing_value_handler.handle(
            working_df, dataset_name=dataset_name, config=config.missing_value_config
        )

        working_df, duplicate_report = self._duplicate_handler.remove(
            working_df, dataset_name=dataset_name, config=config.duplicate_config
        )

        validation_report: dict[str, Any] | None = None
        if config.enable_business_rules:
            validator = config.business_rule_validator or BusinessRuleValidator(
                self._logger
            )
            if config.use_default_business_ruleset and not validator.has_rules():
                validator.build_default_ruleset(list(working_df.columns.astype(str)))
            validation_report = validator.validate(working_df, dataset_name=dataset_name)

        duration = time.perf_counter() - started_at

        result = CleaningResult(
            dataset_name=dataset_name,
            cleaned_dataframe=working_df,
            rows_before=rows_before,
            rows_after=working_df.shape[0],
            columns_before=columns_before,
            columns_after=working_df.shape[1],
            duration_seconds=duration,
            column_report=column_report,
            datatype_report=datatype_report,
            missing_value_report=missing_value_report,
            duplicate_report=duplicate_report,
            validation_report=validation_report,
        )

        self._logger.log_operation(
            dataset_name=dataset_name,
            module=MODULE_NAME,
            operation="clean_dataset",
            message=(
                f"Cleaning complete in {duration:.2f}s: "
                f"rows {rows_before} -> {result.rows_after}, "
                f"columns {columns_before} -> {result.columns_after}."
            ),
            details={
                "rows_before": rows_before,
                "rows_after": result.rows_after,
                "columns_before": columns_before,
                "columns_after": result.columns_after,
                "duration_seconds": round(duration, 4),
            },
        )

        return result


__all__ = ["DataCleaner", "DataCleaningConfig", "CleaningResult"]
