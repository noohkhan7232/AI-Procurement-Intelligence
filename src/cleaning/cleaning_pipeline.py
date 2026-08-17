"""Full execution pipeline for Phase 2.4 — Enterprise Data Cleaning.

:class:`CleaningPipeline` discovers every CSV under ``data/raw/v1``
(recursively, with zero hardcoded filenames — reusing
:class:`src.main.DatasetDiscoveryService`, the same discovery service the
profiling execution pipeline uses, so discovery logic exists in exactly one
place), cleans each one with :class:`~src.cleaning.data_cleaner.DataCleaner`,
saves the cleaned output under ``data/interim/`` (mirroring the dataset's
subdirectory structure relative to the discovery root), and persists five
report types plus an execution summary under ``reports/cleaning/`` using the
same immutable run-versioning model established for profiling reports:

    * ``reports/cleaning/runs/<timestamp>/`` — one permanent, historical
      record per execution. Never overwritten, never deleted by later runs.
    * ``reports/cleaning/latest/`` — cleared at the start of each run and
      populated only with that run's reports.

A failure while cleaning one dataset is isolated and logged; the pipeline
continues with the remaining datasets and reports every failure in the
final Cleaning Summary, mirroring the fault-tolerance model of the
profiling execution pipeline in :mod:`src.main`.

Usage:
    $ python -m src.cleaning.cleaning_pipeline
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any

from src.cleaning.data_cleaner import CleaningResult, DataCleaner, DataCleaningConfig
from src.config.paths import ProjectPaths, get_project_paths
from src.application.dataset_discovery.service import DatasetDiscoveryService
from src.utils.cleaning_logger import CleaningLogger
from src.utils.file_loader import FileLoader, FileLoaderError
from src.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Subdirectory (relative to the platform's ``raw_data_dir``) that this
#: pipeline discovers datasets under. Matches the profiling execution
#: pipeline's discovery location so both stages operate on the same
#: ingestion surface.
RAW_DATA_DISCOVERY_SUBDIR: str = "v1"

#: Glob pattern used for recursive CSV discovery.
CSV_DISCOVERY_PATTERN: str = "*.csv"

#: Timestamp format used to name each immutable run folder.
RUN_TIMESTAMP_FORMAT: str = "%Y%m%d_%H%M%S"

#: JSON serialization indentation width.
JSON_INDENT: int = 2

#: Console divider width for section headers.
CONSOLE_RULE_WIDTH: int = 78


class CleaningExitCode(IntEnum):
    """Process exit codes returned by :func:`main`."""

    SUCCESS = 0
    COMPLETED_WITH_FAILURES = 1
    CRITICAL_FAILURE = 2


class DatasetCleaningStatus(IntEnum):
    """Outcome of cleaning a single dataset."""

    SUCCESS = 0
    FAILURE = 1


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #


@dataclass
class DatasetCleaningOutcome:
    """Outcome of running one dataset through the cleaning pipeline.

    Attributes:
        dataset_name: Human-readable dataset identifier (the file's stem).
        source_path: Absolute path to the source CSV under ``data/raw/v1``.
        output_path: Absolute path to the cleaned CSV under
            ``data/interim/``, if cleaning succeeded.
        status: Whether the dataset succeeded or failed.
        duration_seconds: Wall-clock time spent cleaning this dataset.
        cleaning_result: The full :class:`~src.cleaning.data_cleaner.CleaningResult`,
            if cleaning succeeded.
        error_message: Description of the failure, if any.
    """

    dataset_name: str
    source_path: Path
    status: DatasetCleaningStatus
    duration_seconds: float
    output_path: Path | None = None
    cleaning_result: CleaningResult | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether this dataset was cleaned without error."""
        return self.status is DatasetCleaningStatus.SUCCESS


# --------------------------------------------------------------------------- #
# Per-dataset cleaning + persistence
# --------------------------------------------------------------------------- #


class DatasetCleaningProcessor:
    """Runs a single dataset through load -> clean -> save-to-interim.

    Every dependency is injected, so this class has zero knowledge of how
    files are discovered or how results/reports are aggregated.
    """

    def __init__(
        self,
        file_loader: FileLoader,
        data_cleaner: DataCleaner,
        discovery_root: Path,
        interim_root: Path,
        *,
        cleaning_config: DataCleaningConfig | None = None,
    ) -> None:
        """Initialize the processor with its collaborators.

        Args:
            file_loader: Loads raw CSV files into DataFrames.
            data_cleaner: Coordinates the full cleaning sequence.
            discovery_root: The root directory datasets were discovered
                under, used to compute each dataset's relative output path.
            interim_root: The root directory cleaned datasets are saved
                under.
            cleaning_config: Optional shared cleaning configuration applied
                to every dataset. If ``None``, :class:`DataCleaner` defaults
                are used.
        """
        self._file_loader = file_loader
        self._data_cleaner = data_cleaner
        self._discovery_root = discovery_root
        self._interim_root = interim_root
        self._cleaning_config = cleaning_config

    def process(self, file_path: Path) -> DatasetCleaningOutcome:
        """Process a single dataset file end-to-end.

        Any exception raised at any stage is caught, logged, and converted
        into a failed :class:`DatasetCleaningOutcome` so the caller can
        continue processing the remaining datasets.

        Args:
            file_path: Absolute path to the CSV file to clean.

        Returns:
            A :class:`DatasetCleaningOutcome` describing the result.
        """
        dataset_name = file_path.stem
        started_at = time.perf_counter()

        try:
            logger.info("Cleaning dataset '%s' (%s).", dataset_name, file_path)

            dataframe = self._file_loader.load_csv(file_path)
            result = self._data_cleaner.clean(
                dataframe, dataset_name=dataset_name, config=self._cleaning_config
            )

            output_path = self._resolve_output_path(file_path)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            result.cleaned_dataframe.to_csv(output_path, index=False)

            duration = time.perf_counter() - started_at
            logger.info(
                "Successfully cleaned dataset '%s' in %.2fs -> '%s'.",
                dataset_name,
                duration,
                output_path,
            )

            return DatasetCleaningOutcome(
                dataset_name=dataset_name,
                source_path=file_path,
                status=DatasetCleaningStatus.SUCCESS,
                duration_seconds=duration,
                output_path=output_path,
                cleaning_result=result,
            )

        except FileLoaderError as exc:
            return self._build_failure_outcome(
                dataset_name, file_path, started_at, f"File loading failed: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-dataset failures
            logger.exception("Unexpected error while cleaning '%s'.", dataset_name)
            return self._build_failure_outcome(
                dataset_name, file_path, started_at, f"Unexpected error: {exc}"
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
    def _build_failure_outcome(
        dataset_name: str, file_path: Path, started_at: float, message: str
    ) -> DatasetCleaningOutcome:
        """Build a failed :class:`DatasetCleaningOutcome`.

        Args:
            dataset_name: The dataset identifier.
            file_path: The source file path.
            started_at: The ``time.perf_counter()`` value recorded at the
                start of processing.
            message: A human-readable description of the failure.

        Returns:
            A :class:`DatasetCleaningOutcome` with ``status=FAILURE``.
        """
        duration = time.perf_counter() - started_at
        logger.error(
            "Failed to clean dataset '%s' after %.2fs: %s",
            dataset_name,
            duration,
            message,
        )
        return DatasetCleaningOutcome(
            dataset_name=dataset_name,
            source_path=file_path,
            status=DatasetCleaningStatus.FAILURE,
            duration_seconds=duration,
            error_message=message,
        )


# --------------------------------------------------------------------------- #
# Report persistence (versioned: runs/<timestamp>/ + latest/)
# --------------------------------------------------------------------------- #


class CleaningReportWriter:
    """Persists the five required cleaning reports with immutable run versioning.

    Mirrors the versioning model used by
    :class:`src.profiling.generate_report.ReportGenerator`: every report is
    written into this instance's immutable run folder and mirrored into
    ``latest/``, which is cleared exactly once, at construction time.
    """

    def __init__(self, paths: ProjectPaths, *, run_timestamp: str | None = None) -> None:
        """Initialize the writer and open a new immutable run folder.

        Args:
            paths: Resolved project path configuration.
            run_timestamp: Explicit run identifier. If ``None``, derived
                from the current time.
        """
        self._paths = paths
        self.run_timestamp = run_timestamp or datetime.now().strftime(
            RUN_TIMESTAMP_FORMAT
        )
        self.run_dir = paths.cleaning_runs_dir / self.run_timestamp
        self.latest_dir = paths.cleaning_latest_dir

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._reset_latest_directory()

    def write_reports(
        self,
        outcomes: list[DatasetCleaningOutcome],
        *,
        pipeline_started_at: datetime,
        pipeline_duration_seconds: float,
    ) -> dict[str, Path]:
        """Write all five report types plus the execution summary.

        Args:
            outcomes: Every per-dataset cleaning outcome, success or failure.
            pipeline_started_at: Timestamp marking the start of the run.
            pipeline_duration_seconds: Total wall-clock duration of the run.

        Returns:
            A mapping of report type to the absolute path of the written
            file inside this run's immutable directory.
        """
        successes = [o for o in outcomes if o.succeeded]
        failures = [o for o in outcomes if not o.succeeded]

        paths = {
            "cleaning_summary": self._write_cleaning_summary(
                outcomes,
                successes,
                failures,
                pipeline_started_at=pipeline_started_at,
                pipeline_duration_seconds=pipeline_duration_seconds,
            ),
            "validation_report": self._write_validation_report(successes),
            "transformation_report": self._write_transformation_report(successes),
            "missing_value_report": self._write_missing_value_report(successes),
            "duplicate_report": self._write_duplicate_report(successes),
        }
        return paths

    # ------------------------------------------------------------------ #
    # Individual report builders
    # ------------------------------------------------------------------ #

    def _write_cleaning_summary(
        self,
        outcomes: list[DatasetCleaningOutcome],
        successes: list[DatasetCleaningOutcome],
        failures: list[DatasetCleaningOutcome],
        *,
        pipeline_started_at: datetime,
        pipeline_duration_seconds: float,
    ) -> Path:
        """Write the overall Cleaning Summary report (JSON)."""
        summary = {
            "pipeline": "AI Procurement Intelligence Platform - Cleaning Pipeline",
            "run_timestamp": self.run_timestamp,
            "run_started_at": pipeline_started_at.isoformat(),
            "run_duration_seconds": round(pipeline_duration_seconds, 4),
            "datasets_discovered": len(outcomes),
            "datasets_succeeded": len(successes),
            "datasets_failed": len(failures),
            "datasets": [
                {
                    "dataset_name": o.dataset_name,
                    "source_path": str(o.source_path),
                    "output_path": str(o.output_path) if o.output_path else None,
                    "status": o.status.name,
                    "duration_seconds": round(o.duration_seconds, 4),
                    "rows_before": (
                        o.cleaning_result.rows_before if o.cleaning_result else None
                    ),
                    "rows_after": (
                        o.cleaning_result.rows_after if o.cleaning_result else None
                    ),
                    "columns_before": (
                        o.cleaning_result.columns_before if o.cleaning_result else None
                    ),
                    "columns_after": (
                        o.cleaning_result.columns_after if o.cleaning_result else None
                    ),
                    "error_message": o.error_message,
                }
                for o in outcomes
            ],
        }
        return self._write_json("cleaning_summary.json", summary)

    def _write_validation_report(self, successes: list[DatasetCleaningOutcome]) -> Path:
        """Write the aggregated Validation Report (business rule outcomes)."""
        payload = {
            "run_timestamp": self.run_timestamp,
            "datasets": {
                o.dataset_name: o.cleaning_result.validation_report
                for o in successes
                if o.cleaning_result is not None
            },
        }
        return self._write_json("validation_report.json", payload)

    def _write_transformation_report(
        self, successes: list[DatasetCleaningOutcome]
    ) -> Path:
        """Write the aggregated Transformation Report (columns + dtypes)."""
        payload = {
            "run_timestamp": self.run_timestamp,
            "datasets": {
                o.dataset_name: {
                    "column_standardization": o.cleaning_result.column_report,
                    "datatype_standardization": o.cleaning_result.datatype_report,
                }
                for o in successes
                if o.cleaning_result is not None
            },
        }
        return self._write_json("transformation_report.json", payload)

    def _write_missing_value_report(
        self, successes: list[DatasetCleaningOutcome]
    ) -> Path:
        """Write the aggregated Missing Value Report."""
        payload = {
            "run_timestamp": self.run_timestamp,
            "datasets": {
                o.dataset_name: o.cleaning_result.missing_value_report
                for o in successes
                if o.cleaning_result is not None
            },
        }
        return self._write_json("missing_value_report.json", payload)

    def _write_duplicate_report(self, successes: list[DatasetCleaningOutcome]) -> Path:
        """Write the aggregated Duplicate Report."""
        payload = {
            "run_timestamp": self.run_timestamp,
            "datasets": {
                o.dataset_name: o.cleaning_result.duplicate_report
                for o in successes
                if o.cleaning_result is not None
            },
        }
        return self._write_json("duplicate_report.json", payload)

    # ------------------------------------------------------------------ #
    # Versioning internals
    # ------------------------------------------------------------------ #

    def _write_json(self, file_name: str, payload: dict[str, Any]) -> Path:
        """Write a JSON payload into the run folder and mirror it to ``latest/``.

        Args:
            file_name: The report file name (e.g. ``'cleaning_summary.json'``).
            payload: The JSON-serializable content to write.

        Returns:
            The absolute path to the written file inside the run folder.
        """
        output_path = self.run_dir / file_name
        with output_path.open("w", encoding="utf-8") as json_file:
            json.dump(payload, json_file, indent=JSON_INDENT, default=str)
        shutil.copy2(output_path, self.latest_dir / file_name)
        logger.info("Report written to '%s' (mirrored to latest/).", output_path)
        return output_path

    def _reset_latest_directory(self) -> None:
        """Clear ``latest/`` so it no longer reflects a prior, unrelated run."""
        if self.latest_dir.exists():
            shutil.rmtree(self.latest_dir)
        self.latest_dir.mkdir(parents=True, exist_ok=True)


# --------------------------------------------------------------------------- #
# Console reporting
# --------------------------------------------------------------------------- #


class CleaningConsoleReporter:
    """Renders professional progress updates and final statistics to stdout."""

    def print_header(self, dataset_count: int, discovery_root: Path) -> None:
        """Print the pipeline start banner."""
        self._print_rule()
        print("AI PROCUREMENT INTELLIGENCE PLATFORM — DATA CLEANING PIPELINE")
        self._print_rule()
        print(f"Discovery root : {discovery_root}")
        print(f"Datasets found : {dataset_count}")
        self._print_rule()

    def print_progress(
        self, index: int, total: int, outcome: DatasetCleaningOutcome
    ) -> None:
        """Print a single-line progress update for one cleaned dataset."""
        status_label = "OK  " if outcome.succeeded else "FAIL"
        if outcome.succeeded and outcome.cleaning_result is not None:
            result = outcome.cleaning_result
            violations = (
                result.validation_report.get("total_violations")
                if result.validation_report
                else "n/a"
            )
            detail = (
                f"rows={result.rows_before}->{result.rows_after} "
                f"cols={result.columns_before}->{result.columns_after} "
                f"violations={violations}"
            )
        else:
            detail = f"error={outcome.error_message}"

        print(
            f"[{index:>3}/{total:<3}] [{status_label}] "
            f"{outcome.dataset_name:<30} ({outcome.duration_seconds:.2f}s)  {detail}"
        )

    def print_summary(
        self,
        outcomes: list[DatasetCleaningOutcome],
        *,
        total_duration_seconds: float,
        report_paths: dict[str, Path] | None,
    ) -> None:
        """Print final execution statistics."""
        successes = [o for o in outcomes if o.succeeded]
        failures = [o for o in outcomes if not o.succeeded]

        self._print_rule()
        print("CLEANING EXECUTION SUMMARY")
        self._print_rule()
        print(f"Total datasets   : {len(outcomes)}")
        print(f"Succeeded        : {len(successes)}")
        print(f"Failed           : {len(failures)}")
        print(f"Total duration   : {total_duration_seconds:.2f}s")
        if report_paths is not None:
            for report_type, path in report_paths.items():
                print(f"{report_type:<20} : {path}")

        if failures:
            print()
            print("FAILURES:")
            for outcome in failures:
                print(f"  - {outcome.dataset_name}: {outcome.error_message}")

        self._print_rule()

    @staticmethod
    def _print_rule() -> None:
        """Print a horizontal divider line."""
        print("=" * CONSOLE_RULE_WIDTH)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


class CleaningPipeline:
    """Coordinates discovery, cleaning, report generation, and console output.

    Supports future extension in two ways without modifying this class:
    injecting a custom :class:`~src.cleaning.data_cleaner.DataCleaner`
    (e.g. pre-configured with dataset-specific business rules) and/or a
    custom :class:`~src.config.paths.ProjectPaths`/discovery root, keeping
    the orchestration logic itself stable (Open/Closed Principle).
    """

    def __init__(
        self,
        *,
        data_cleaner: DataCleaner | None = None,
        cleaning_config: DataCleaningConfig | None = None,
        discovery_root: Path | None = None,
        run_timestamp: str | None = None,
    ) -> None:
        """Initialize the cleaning pipeline and its collaborators.

        Args:
            data_cleaner: The coordinator used to clean each dataset. If
                ``None``, a default :class:`~src.cleaning.data_cleaner.DataCleaner`
                is created.
            cleaning_config: Shared cleaning configuration applied to every
                discovered dataset. If ``None``, per-dataset defaults are
                used (see :class:`~src.cleaning.data_cleaner.DataCleaningConfig`).
            discovery_root: Directory to recursively search for CSV
                datasets. If ``None``, defaults to
                ``<raw_data_dir>/v1``.
            run_timestamp: Explicit run identifier, primarily for
                deterministic testing. If ``None``, derived from the
                current time.
        """
        self._paths = get_project_paths()
        self._discovery_root = discovery_root or (
            self._paths.raw_data_dir / RAW_DATA_DISCOVERY_SUBDIR
        )
        self._discovery_service = DatasetDiscoveryService(
            self._discovery_root, pattern=CSV_DISCOVERY_PATTERN
        )
        self._data_cleaner = data_cleaner or DataCleaner()
        self._processor = DatasetCleaningProcessor(
            file_loader=FileLoader(),
            data_cleaner=self._data_cleaner,
            discovery_root=self._discovery_root,
            interim_root=self._paths.interim_data_dir,
            cleaning_config=cleaning_config,
        )
        self._report_writer = CleaningReportWriter(
            self._paths, run_timestamp=run_timestamp
        )
        self._console_reporter = CleaningConsoleReporter()

    def run(self) -> CleaningExitCode:
        """Execute the full cleaning pipeline end-to-end.

        Returns:
            The :class:`CleaningExitCode` reflecting the outcome of the run.
        """
        pipeline_started_at = datetime.now()
        pipeline_start_counter = time.perf_counter()

        logger.info("Cleaning pipeline starting.")

        try:
            dataset_files = self._discovery_service.discover()
        except Exception:  # noqa: BLE001 - discovery failure is critical
            logger.exception("Critical failure during dataset discovery.")
            return CleaningExitCode.CRITICAL_FAILURE

        self._console_reporter.print_header(len(dataset_files), self._discovery_root)

        if not dataset_files:
            logger.warning("No datasets discovered; nothing to clean.")
            total_duration = time.perf_counter() - pipeline_start_counter
            self._console_reporter.print_summary(
                [], total_duration_seconds=total_duration, report_paths=None
            )
            return CleaningExitCode.COMPLETED_WITH_FAILURES

        outcomes: list[DatasetCleaningOutcome] = []
        for index, file_path in enumerate(dataset_files, start=1):
            outcome = self._processor.process(file_path)
            outcomes.append(outcome)
            self._console_reporter.print_progress(index, len(dataset_files), outcome)

        total_duration = time.perf_counter() - pipeline_start_counter

        report_paths: dict[str, Path] | None = None
        try:
            report_paths = self._report_writer.write_reports(
                outcomes,
                pipeline_started_at=pipeline_started_at,
                pipeline_duration_seconds=total_duration,
            )
        except Exception:  # noqa: BLE001 - report persistence must not crash the run
            logger.exception("Failed to persist cleaning reports.")

        self._console_reporter.print_summary(
            outcomes,
            total_duration_seconds=total_duration,
            report_paths=report_paths,
        )

        failure_count = sum(1 for o in outcomes if not o.succeeded)
        if failure_count > 0:
            logger.warning(
                "Cleaning pipeline finished with %d failure(s) out of %d dataset(s).",
                failure_count,
                len(outcomes),
            )
            return CleaningExitCode.COMPLETED_WITH_FAILURES

        logger.info("Cleaning pipeline finished successfully: all datasets cleaned.")
        return CleaningExitCode.SUCCESS


def main() -> int:
    """Application entry point for the standalone cleaning pipeline.

    Returns:
        The integer process exit code (see :class:`CleaningExitCode`).
    """
    try:
        pipeline = CleaningPipeline()
        exit_code = pipeline.run()
    except Exception:  # noqa: BLE001 - last-resort safety net at the entry point
        logger.exception("Critical, unhandled failure in the cleaning pipeline.")
        return int(CleaningExitCode.CRITICAL_FAILURE)

    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "CleaningPipeline",
    "CleaningExitCode",
    "DatasetCleaningStatus",
    "DatasetCleaningOutcome",
    "DatasetCleaningProcessor",
    "CleaningReportWriter",
    "CleaningConsoleReporter",
    "main",
]
