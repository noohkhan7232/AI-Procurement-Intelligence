"""Phase 2.4.1 — Enterprise Cleaning Execution Pipeline.

:class:`CleaningExecutor` is a production-grade orchestrator that
automatically cleans every CSV dataset discovered under ``data/raw/v1/``
using the *existing* cleaning framework — it introduces no new cleaning
logic of its own. Every transformation is performed by
:class:`~src.cleaning.data_cleaner.DataCleaner`, which in turn coordinates
(in order) :class:`~src.cleaning.column_standardizer.ColumnStandardizer`,
:class:`~src.cleaning.datatype_standardizer.DataTypeStandardizer`,
:class:`~src.cleaning.missing_value_handler.MissingValueHandler`,
:class:`~src.cleaning.duplicate_handler.DuplicateHandler`, and
:class:`~src.cleaning.business_rule_validator.BusinessRuleValidator`. This
module's only responsibilities are: discovering datasets, invoking the
cleaner once per dataset, persisting cleaned output and reports, isolating
per-dataset failures, and rendering execution progress/statistics.

Every collaborator (file loader, data cleaner, discovery root, project
paths, clock) is constructor-injected, so :class:`CleaningExecutor` can be
fully unit tested with fakes/mocks and requires no changes to run against a
different discovery root, cleaning configuration, or storage location.

Usage:
    $ python -m src.cleaning.cleaning_executor
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any

from src.cleaning.data_cleaner import CleaningResult, DataCleaner, DataCleaningConfig
from src.config.paths import ProjectPaths, get_project_paths
from src.main import DatasetDiscoveryService
from src.utils.cleaning_logger import CleaningLogger
from src.utils.file_loader import FileLoader, FileLoaderError
from src.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Subdirectory (relative to the platform's ``raw_data_dir``) that this
#: executor discovers datasets under.
RAW_DATA_DISCOVERY_SUBDIR: str = "v1"

#: Glob pattern used for recursive CSV discovery.
CSV_DISCOVERY_PATTERN: str = "*.csv"

#: Timestamp format used to name each immutable report run folder.
RUN_TIMESTAMP_FORMAT: str = "%Y%m%d_%H%M%S"

#: JSON serialization indentation width.
JSON_INDENT: int = 2

#: Console divider width, matching the platform's standard console banner.
CONSOLE_RULE_WIDTH: int = 40


class ExecutorExitCode(IntEnum):
    """Process exit codes returned by :func:`main`."""

    SUCCESS = 0
    COMPLETED_WITH_FAILURES = 1
    CRITICAL_FAILURE = 2


class DatasetExecutionStatus(IntEnum):
    """Outcome of executing the cleaning framework against one dataset."""

    SUCCESS = 0
    FAILURE = 1


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #


@dataclass
class DatasetExecutionRecord:
    """The complete outcome of cleaning a single dataset.

    Attributes:
        dataset_name: Human-readable dataset identifier (the file's stem).
        source_path: Absolute path to the source CSV under ``data/raw/v1``.
        status: Whether the dataset succeeded or failed.
        processing_time_seconds: Wall-clock time spent on this dataset.
        output_path: Absolute path to the cleaned CSV under
            ``data/interim/``, if cleaning succeeded.
        cleaning_result: The full :class:`~src.cleaning.data_cleaner.CleaningResult`
            produced by the cleaning framework, if cleaning succeeded.
        error_message: Description of the failure, if any.
    """

    dataset_name: str
    source_path: Path
    status: DatasetExecutionStatus
    processing_time_seconds: float
    output_path: Path | None = None
    cleaning_result: CleaningResult | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether this dataset was cleaned without error."""
        return self.status is DatasetExecutionStatus.SUCCESS

    @property
    def rows_before(self) -> int | None:
        """Row count prior to cleaning, or ``None`` if cleaning failed."""
        return self.cleaning_result.rows_before if self.cleaning_result else None

    @property
    def rows_after(self) -> int | None:
        """Row count after cleaning, or ``None`` if cleaning failed."""
        return self.cleaning_result.rows_after if self.cleaning_result else None

    @property
    def duplicates_removed(self) -> int | None:
        """Number of duplicate rows removed, or ``None`` if cleaning failed."""
        if self.cleaning_result is None:
            return None
        return self.cleaning_result.duplicate_report.get("duplicates_removed", 0)

    @property
    def missing_values_fixed(self) -> int | None:
        """Total missing values filled across all columns, or ``None`` if failed."""
        if self.cleaning_result is None:
            return None
        columns = self.cleaning_result.missing_value_report.get("columns", {})
        return sum(col.get("values_filled", 0) for col in columns.values())

    @property
    def columns_standardized(self) -> int | None:
        """Number of column names renamed to snake_case, or ``None`` if failed."""
        if self.cleaning_result is None:
            return None
        return self.cleaning_result.column_report.get("columns_renamed", 0)


@dataclass
class ExecutionStatistics:
    """Aggregate execution statistics across every processed dataset.

    Attributes:
        datasets_processed: Total number of datasets discovered and attempted.
        datasets_succeeded: Number of datasets cleaned successfully.
        datasets_failed: Number of datasets that failed to clean.
        total_processing_time_seconds: Sum of every dataset's processing time.
        total_rows_before: Sum of row counts prior to cleaning (successes only).
        total_rows_after: Sum of row counts after cleaning (successes only).
        total_duplicates_removed: Sum of duplicate rows removed across datasets.
        total_missing_values_fixed: Sum of missing values filled across datasets.
        total_columns_standardized: Sum of column renames across datasets.
    """

    datasets_processed: int = 0
    datasets_succeeded: int = 0
    datasets_failed: int = 0
    total_processing_time_seconds: float = 0.0
    total_rows_before: int = 0
    total_rows_after: int = 0
    total_duplicates_removed: int = 0
    total_missing_values_fixed: int = 0
    total_columns_standardized: int = 0

    @classmethod
    def from_records(
        cls, records: list[DatasetExecutionRecord]
    ) -> "ExecutionStatistics":
        """Aggregate statistics from a list of per-dataset execution records.

        Args:
            records: Every per-dataset execution record, success or failure.

        Returns:
            The aggregated :class:`ExecutionStatistics`.
        """
        successes = [r for r in records if r.succeeded]
        return cls(
            datasets_processed=len(records),
            datasets_succeeded=len(successes),
            datasets_failed=len(records) - len(successes),
            total_processing_time_seconds=round(
                sum(r.processing_time_seconds for r in records), 4
            ),
            total_rows_before=sum(r.rows_before or 0 for r in successes),
            total_rows_after=sum(r.rows_after or 0 for r in successes),
            total_duplicates_removed=sum(r.duplicates_removed or 0 for r in successes),
            total_missing_values_fixed=sum(
                r.missing_values_fixed or 0 for r in successes
            ),
            total_columns_standardized=sum(
                r.columns_standardized or 0 for r in successes
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dictionary."""
        return {
            "datasets_processed": self.datasets_processed,
            "datasets_succeeded": self.datasets_succeeded,
            "datasets_failed": self.datasets_failed,
            "total_processing_time_seconds": self.total_processing_time_seconds,
            "total_rows_before": self.total_rows_before,
            "total_rows_after": self.total_rows_after,
            "total_duplicates_removed": self.total_duplicates_removed,
            "total_missing_values_fixed": self.total_missing_values_fixed,
            "total_columns_standardized": self.total_columns_standardized,
        }


# --------------------------------------------------------------------------- #
# Per-dataset execution
# --------------------------------------------------------------------------- #


class DatasetCleaningRunner:
    """Runs one dataset through load -> clean (via DataCleaner) -> save.

    This class performs no cleaning itself — it delegates every
    transformation to the injected :class:`~src.cleaning.data_cleaner.DataCleaner`,
    which is the existing, already-tested coordinator for
    ColumnStandardizer -> DataTypeStandardizer -> MissingValueHandler ->
    DuplicateHandler -> BusinessRuleValidator.
    """

    def __init__(
        self,
        *,
        file_loader: FileLoader,
        data_cleaner: DataCleaner,
        discovery_root: Path,
        interim_root: Path,
        cleaning_config: DataCleaningConfig | None = None,
    ) -> None:
        """Initialize the runner with its collaborators.

        Args:
            file_loader: Loads raw CSV files into DataFrames.
            data_cleaner: The existing cleaning coordinator to delegate to.
            discovery_root: The root directory datasets were discovered
                under, used to compute each dataset's relative output path.
            interim_root: The root directory cleaned datasets are saved
                under.
            cleaning_config: Optional shared cleaning configuration applied
                to every dataset.
        """
        self._file_loader = file_loader
        self._data_cleaner = data_cleaner
        self._discovery_root = discovery_root
        self._interim_root = interim_root
        self._cleaning_config = cleaning_config

    def run(self, source_path: Path) -> DatasetExecutionRecord:
        """Clean a single dataset end-to-end and persist the result.

        Any exception raised at any stage is caught, logged, and converted
        into a failed :class:`DatasetExecutionRecord` so the caller can
        continue processing the remaining datasets.

        Args:
            source_path: Absolute path to the source CSV file.

        Returns:
            A :class:`DatasetExecutionRecord` describing the outcome.
        """
        dataset_name = source_path.stem
        started_at = time.perf_counter()

        try:
            dataframe = self._file_loader.load_csv(source_path)

            result = self._data_cleaner.clean(
                dataframe, dataset_name=dataset_name, config=self._cleaning_config
            )

            output_path = self._resolve_output_path(source_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            result.cleaned_dataframe.to_csv(output_path, index=False)

            processing_time = time.perf_counter() - started_at
            logger.info(
                "Cleaned dataset '%s' in %.2fs -> '%s'.",
                dataset_name,
                processing_time,
                output_path,
            )

            return DatasetExecutionRecord(
                dataset_name=dataset_name,
                source_path=source_path,
                status=DatasetExecutionStatus.SUCCESS,
                processing_time_seconds=processing_time,
                output_path=output_path,
                cleaning_result=result,
            )

        except FileLoaderError as exc:
            return self._build_failure_record(
                dataset_name, source_path, started_at, f"File loading failed: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-dataset failures
            logger.exception("Unexpected error while cleaning '%s'.", dataset_name)
            return self._build_failure_record(
                dataset_name, source_path, started_at, f"Unexpected error: {exc}"
            )

    def _resolve_output_path(self, source_path: Path) -> Path:
        """Compute the interim output path, mirroring the source's subpath.

        Args:
            source_path: The absolute path to the source CSV.

        Returns:
            The absolute path under ``interim_root`` where the cleaned
            dataset should be saved, preserving the same relative
            subdirectory structure the file had under ``discovery_root``.
        """
        try:
            relative_path = source_path.relative_to(self._discovery_root)
        except ValueError:
            relative_path = Path(source_path.name)
        return self._interim_root / relative_path

    @staticmethod
    def _build_failure_record(
        dataset_name: str, source_path: Path, started_at: float, message: str
    ) -> DatasetExecutionRecord:
        """Build a failed :class:`DatasetExecutionRecord`.

        Args:
            dataset_name: The dataset identifier.
            source_path: The source file path.
            started_at: The ``time.perf_counter()`` value recorded at the
                start of processing.
            message: A human-readable description of the failure.

        Returns:
            A :class:`DatasetExecutionRecord` with ``status=FAILURE``.
        """
        processing_time = time.perf_counter() - started_at
        logger.error(
            "Failed to clean dataset '%s' after %.2fs: %s",
            dataset_name,
            processing_time,
            message,
        )
        return DatasetExecutionRecord(
            dataset_name=dataset_name,
            source_path=source_path,
            status=DatasetExecutionStatus.FAILURE,
            processing_time_seconds=processing_time,
            error_message=message,
        )


# --------------------------------------------------------------------------- #
# Report persistence (versioned: runs/<timestamp>/ + latest/)
# --------------------------------------------------------------------------- #


class ExecutionReportWriter:
    """Persists the Cleaning Summary, Transformation Summary, Validation
    Summary, and Execution Statistics reports for one executor run.

    Uses the same immutable run-versioning model as the rest of the
    platform's report generators: every report is written into this
    instance's dedicated run folder under ``reports/cleaning/runs/`` (never
    overwritten by later runs) and mirrored into ``reports/cleaning/latest/``
    (cleared once, at construction time).
    """

    def __init__(self, paths: ProjectPaths, *, run_timestamp: str | None = None) -> None:
        """Initialize the writer and open a new immutable run folder.

        Args:
            paths: Resolved project path configuration.
            run_timestamp: Explicit run identifier. If ``None``, derived
                from the current time.
        """
        self.run_timestamp = run_timestamp or datetime.now().strftime(
            RUN_TIMESTAMP_FORMAT
        )
        self.run_dir = paths.cleaning_runs_dir / self.run_timestamp
        self.latest_dir = paths.cleaning_latest_dir

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._reset_latest_directory()

    def write_reports(
        self,
        records: list[DatasetExecutionRecord],
        *,
        started_at: datetime,
        statistics: ExecutionStatistics,
    ) -> dict[str, Path]:
        """Write every required report for this run.

        Args:
            records: Every per-dataset execution record, success or failure.
            started_at: Timestamp marking the start of the run.
            statistics: Aggregated execution statistics for the run.

        Returns:
            A mapping of report type to the absolute path of the written
            file inside this run's immutable directory.
        """
        successes = [r for r in records if r.succeeded]

        return {
            "cleaning_summary": self._write_json(
                "cleaning_summary.json",
                self._build_cleaning_summary(records, started_at, statistics),
            ),
            "transformation_summary": self._write_json(
                "transformation_summary.json",
                self._build_transformation_summary(successes),
            ),
            "validation_summary": self._write_json(
                "validation_summary.json", self._build_validation_summary(successes)
            ),
            "execution_statistics": self._write_json(
                "execution_statistics.json",
                self._build_execution_statistics(records, statistics),
            ),
        }

    # ------------------------------------------------------------------ #
    # Report builders
    # ------------------------------------------------------------------ #

    def _build_cleaning_summary(
        self,
        records: list[DatasetExecutionRecord],
        started_at: datetime,
        statistics: ExecutionStatistics,
    ) -> dict[str, Any]:
        """Build the master Cleaning Summary payload."""
        return {
            "pipeline": "AI Procurement Intelligence Platform - Cleaning Engine",
            "run_timestamp": self.run_timestamp,
            "run_started_at": started_at.isoformat(),
            "statistics": statistics.as_dict(),
            "datasets": [
                {
                    "dataset_name": r.dataset_name,
                    "source_path": str(r.source_path),
                    "output_path": str(r.output_path) if r.output_path else None,
                    "status": r.status.name,
                    "processing_time_seconds": round(r.processing_time_seconds, 4),
                    "rows_before": r.rows_before,
                    "rows_after": r.rows_after,
                    "duplicates_removed": r.duplicates_removed,
                    "missing_values_fixed": r.missing_values_fixed,
                    "columns_standardized": r.columns_standardized,
                    "error_message": r.error_message,
                }
                for r in records
            ],
        }

    def _build_transformation_summary(
        self, successes: list[DatasetExecutionRecord]
    ) -> dict[str, Any]:
        """Build the Transformation Summary payload (column + dtype changes)."""
        return {
            "run_timestamp": self.run_timestamp,
            "datasets": {
                r.dataset_name: {
                    "column_standardization": r.cleaning_result.column_report,
                    "datatype_standardization": r.cleaning_result.datatype_report,
                }
                for r in successes
                if r.cleaning_result is not None
            },
        }

    def _build_validation_summary(
        self, successes: list[DatasetExecutionRecord]
    ) -> dict[str, Any]:
        """Build the Validation Summary payload (business rule outcomes)."""
        return {
            "run_timestamp": self.run_timestamp,
            "datasets": {
                r.dataset_name: r.cleaning_result.validation_report
                for r in successes
                if r.cleaning_result is not None
            },
        }

    def _build_execution_statistics(
        self,
        records: list[DatasetExecutionRecord],
        statistics: ExecutionStatistics,
    ) -> dict[str, Any]:
        """Build the Execution Statistics payload (per-dataset + aggregate)."""
        return {
            "run_timestamp": self.run_timestamp,
            "aggregate": statistics.as_dict(),
            "per_dataset": [
                {
                    "dataset_name": r.dataset_name,
                    "processing_time_seconds": round(r.processing_time_seconds, 4),
                    "rows_before": r.rows_before,
                    "rows_after": r.rows_after,
                    "duplicates_removed": r.duplicates_removed,
                    "missing_values_fixed": r.missing_values_fixed,
                    "columns_standardized": r.columns_standardized,
                }
                for r in records
                if r.succeeded
            ],
        }

    # ------------------------------------------------------------------ #
    # Versioning internals
    # ------------------------------------------------------------------ #

    def _write_json(self, file_name: str, payload: dict[str, Any]) -> Path:
        """Write a JSON payload into the run folder and mirror it to ``latest/``.

        Args:
            file_name: The report file name.
            payload: The JSON-serializable content to write.

        Returns:
            The absolute path to the written file inside the run folder.
        """
        output_path = self.run_dir / file_name
        with output_path.open("w", encoding="utf-8") as json_file:
            json.dump(payload, json_file, indent=JSON_INDENT, default=str)
        shutil.copy2(output_path, self.latest_dir / file_name)
        return output_path

    def _reset_latest_directory(self) -> None:
        """Clear ``latest/`` so it no longer reflects a prior, unrelated run."""
        if self.latest_dir.exists():
            shutil.rmtree(self.latest_dir)
        self.latest_dir.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Console reporting
# --------------------------------------------------------------------------- #


class ExecutorConsoleReporter:
    """Renders the executor's console banner, per-dataset progress, and footer."""

    def print_banner(self) -> None:
        """Print the fixed platform/engine banner."""
        self._print_rule()
        print("AI PROCUREMENT INTELLIGENCE PLATFORM")
        print("Cleaning Engine")
        self._print_rule()

    def print_discovery(self, dataset_count: int) -> None:
        """Print the number of datasets found.

        Args:
            dataset_count: Number of datasets discovered.
        """
        print(f"Found {dataset_count} datasets")

    def print_dataset_start(self, dataset_name: str) -> None:
        """Print the start-of-processing line for one dataset.

        Args:
            dataset_name: The dataset about to be cleaned.
        """
        print(f"Cleaning {dataset_name}...")

    def print_dataset_result(self, record: DatasetExecutionRecord) -> None:
        """Print the outcome line for one processed dataset.

        Args:
            record: The dataset's execution record.
        """
        if record.succeeded:
            print("OK")
        else:
            print(f"FAILED: {record.error_message}")

    def print_footer(self, statistics: ExecutionStatistics) -> None:
        """Print the closing execution summary block.

        Args:
            statistics: Aggregated execution statistics for the run.
        """
        print()
        if statistics.datasets_failed == 0:
            print("Cleaning completed successfully")
        else:
            print("Cleaning completed with failures")
        print(f"Datasets Processed : {statistics.datasets_processed}")
        print(f"Failed : {statistics.datasets_failed}")
        print("Cleaned datasets saved")
        print("Execution Summary generated")
        self._print_rule()

    @staticmethod
    def _print_rule() -> None:
        """Print a horizontal divider line."""
        print("=" * CONSOLE_RULE_WIDTH)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


class CleaningExecutor:
    """Enterprise execution pipeline that runs the existing cleaning
    framework against every dataset discovered under ``data/raw/v1/``.

    Every collaborator is injected via the constructor (Dependency
    Inversion), so the executor can be tested with fakes and reconfigured
    (different discovery root, cleaning rules, storage paths) without any
    change to its orchestration logic.
    """

    def __init__(
        self,
        *,
        data_cleaner: DataCleaner | None = None,
        file_loader: FileLoader | None = None,
        cleaning_config: DataCleaningConfig | None = None,
        paths: ProjectPaths | None = None,
        discovery_root: Path | None = None,
        run_timestamp: str | None = None,
        console_reporter: ExecutorConsoleReporter | None = None,
    ) -> None:
        """Initialize the cleaning executor and its collaborators.

        Args:
            data_cleaner: The existing cleaning coordinator to delegate all
                transformation work to. If ``None``, a default
                :class:`~src.cleaning.data_cleaner.DataCleaner` is created.
            file_loader: Loads raw CSV files. If ``None``, a default
                :class:`~src.utils.file_loader.FileLoader` is created.
            cleaning_config: Shared cleaning configuration applied to every
                discovered dataset. If ``None``, per-dataset defaults from
                :class:`~src.cleaning.data_cleaner.DataCleaningConfig` apply.
            paths: Resolved project path configuration. If ``None``,
                resolved via :func:`src.config.paths.get_project_paths`.
            discovery_root: Directory to recursively search for CSV
                datasets. If ``None``, defaults to ``<raw_data_dir>/v1``.
            run_timestamp: Explicit run identifier, primarily for
                deterministic testing. If ``None``, derived from the
                current time.
            console_reporter: Renders console output. If ``None``, a
                default :class:`ExecutorConsoleReporter` is used.
        """
        self._paths = paths or get_project_paths()
        self._discovery_root = discovery_root or (
            self._paths.raw_data_dir / RAW_DATA_DISCOVERY_SUBDIR
        )
        self._discovery_service = DatasetDiscoveryService(
            self._discovery_root, pattern=CSV_DISCOVERY_PATTERN
        )

        cleaning_logger = CleaningLogger(__name__)
        self._data_cleaner = data_cleaner or DataCleaner(cleaning_logger)
        self._runner = DatasetCleaningRunner(
            file_loader=file_loader or FileLoader(),
            data_cleaner=self._data_cleaner,
            discovery_root=self._discovery_root,
            interim_root=self._paths.interim_data_dir,
            cleaning_config=cleaning_config,
        )
        self._report_writer = ExecutionReportWriter(
            self._paths, run_timestamp=run_timestamp
        )
        self._console = console_reporter or ExecutorConsoleReporter()

    def execute(self) -> ExecutorExitCode:
        """Run the full cleaning execution pipeline end-to-end.

        Returns:
            The :class:`ExecutorExitCode` reflecting the outcome of the run.
        """
        started_at = datetime.now()

        self._console.print_banner()
        logger.info("Cleaning executor starting.")

        try:
            dataset_files = self._discovery_service.discover()
        except Exception:  # noqa: BLE001 - discovery failure is critical
            logger.exception("Critical failure during dataset discovery.")
            return ExecutorExitCode.CRITICAL_FAILURE

        self._console.print_discovery(len(dataset_files))

        if not dataset_files:
            logger.warning("No datasets discovered under '%s'.", self._discovery_root)
            statistics = ExecutionStatistics.from_records([])
            self._console.print_footer(statistics)
            return ExecutorExitCode.COMPLETED_WITH_FAILURES

        records: list[DatasetExecutionRecord] = []
        for source_path in dataset_files:
            dataset_name = source_path.stem
            self._console.print_dataset_start(dataset_name)

            record = self._runner.run(source_path)
            records.append(record)

            self._console.print_dataset_result(record)

        statistics = ExecutionStatistics.from_records(records)

        try:
            self._report_writer.write_reports(
                records, started_at=started_at, statistics=statistics
            )
        except Exception:  # noqa: BLE001 - report persistence must not crash the run
            logger.exception("Failed to persist cleaning execution reports.")

        self._console.print_footer(statistics)

        if statistics.datasets_failed > 0:
            logger.warning(
                "Cleaning executor finished with %d failure(s) out of %d dataset(s).",
                statistics.datasets_failed,
                statistics.datasets_processed,
            )
            return ExecutorExitCode.COMPLETED_WITH_FAILURES

        logger.info("Cleaning executor finished successfully: all datasets cleaned.")
        return ExecutorExitCode.SUCCESS


def main() -> int:
    """Application entry point for the standalone cleaning executor.

    Returns:
        The integer process exit code (see :class:`ExecutorExitCode`).
    """
    try:
        executor = CleaningExecutor()
        exit_code = executor.execute()
    except Exception:  # noqa: BLE001 - last-resort safety net at the entry point
        logger.exception("Critical, unhandled failure in the cleaning executor.")
        return int(ExecutorExitCode.CRITICAL_FAILURE)

    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "CleaningExecutor",
    "ExecutorExitCode",
    "DatasetExecutionStatus",
    "DatasetExecutionRecord",
    "ExecutionStatistics",
    "DatasetCleaningRunner",
    "ExecutionReportWriter",
    "ExecutorConsoleReporter",
    "main",
]