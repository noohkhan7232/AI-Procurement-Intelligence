"""Application entry point — Phase 2.3 Execution Pipeline.

Discovers every CSV dataset under ``data/raw/v1`` (recursively, with zero
hardcoded filenames), profiles and quality-checks each one using the
existing :mod:`src.profiling` framework, generates a per-dataset report
bundle plus a single master summary report, and prints professional
execution statistics to the console.

Design overview (Clean Architecture / SOLID):
    * :class:`DatasetDiscoveryService` — sole responsibility: find CSV files.
    * :class:`DatasetProcessor` — sole responsibility: run one dataset
      through load -> profile -> quality-check -> report, in isolation.
    * :class:`ExecutionSummaryBuilder` — sole responsibility: aggregate
      per-dataset results into a master summary and persist it.
    * :class:`ConsoleReporter` — sole responsibility: render progress and
      final statistics to the console.
    * :class:`ExecutionPipeline` — orchestrates the above via dependency
      injection; contains no I/O or business logic of its own.

Each collaborator depends only on abstractions it needs (constructor
injection), making every piece independently unit-testable and replaceable
without modifying this module (Open/Closed Principle).

Exit codes:
    0 -- All discovered datasets were processed successfully.
    1 -- The pipeline ran to completion but one or more datasets failed,
         or zero datasets were discovered.
    2 -- A critical, unrecoverable error stopped the pipeline itself
         (e.g. the discovery root could not be accessed).

Usage:
    $ python -m src.main
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import IntEnum
from pathlib import Path
from typing import Any

from src.config.paths import ProjectPaths, get_project_paths
from src.profiling.data_profiler import DataProfiler
from src.profiling.generate_report import ReportGenerator
from src.profiling.quality_checker import DataQualityChecker
from src.utils.file_loader import FileLoader, FileLoaderError
from src.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Subdirectory (relative to the platform's ``raw_data_dir``) that this
#: pipeline discovers datasets under. Only this constant needs to change if
#: the ingestion location moves; no filenames are ever hardcoded.
RAW_DATA_DISCOVERY_SUBDIR: str = "v1"

#: Glob pattern used for recursive CSV discovery.
CSV_DISCOVERY_PATTERN: str = "*.csv"

#: Console divider width for section headers.
CONSOLE_RULE_WIDTH: int = 78

#: Timestamp format used for the master summary report filenames.
SUMMARY_TIMESTAMP_FORMAT: str = "%Y%m%d_%H%M%S"


class ExitCode(IntEnum):
    """Process exit codes returned by :func:`main`."""

    SUCCESS = 0
    COMPLETED_WITH_FAILURES = 1
    CRITICAL_FAILURE = 2


class DatasetStatus(IntEnum):
    """Outcome of processing a single dataset."""

    SUCCESS = 0
    FAILURE = 1


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #


@dataclass
class DatasetExecutionResult:
    """Outcome of running one dataset through the profiling pipeline.

    Attributes:
        dataset_name: Human-readable dataset identifier (derived from the
            file's stem).
        file_path: Absolute path to the source CSV file.
        status: Whether the dataset succeeded or failed.
        duration_seconds: Wall-clock time spent processing this dataset.
        row_count: Number of rows in the dataset, if successfully loaded.
        column_count: Number of columns in the dataset, if successfully
            loaded.
        overall_quality_score: The overall quality score (0-100), if
            successfully computed.
        warning_count: Number of data quality warnings raised, if
            successfully computed.
        report_paths: Mapping of report type to output file path, if
            reports were generated successfully.
        error_message: Description of the failure, if any.
    """

    dataset_name: str
    file_path: Path
    status: DatasetStatus
    duration_seconds: float
    row_count: int | None = None
    column_count: int | None = None
    overall_quality_score: float | None = None
    warning_count: int | None = None
    report_paths: dict[str, Path] = field(default_factory=dict)
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether this dataset was processed without error."""
        return self.status is DatasetStatus.SUCCESS


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #


class DatasetDiscoveryService:
    """Discovers dataset files on disk. Single responsibility: discovery only."""

    def __init__(self, search_root: Path, *, pattern: str = CSV_DISCOVERY_PATTERN) -> None:
        """Initialize the discovery service.

        Args:
            search_root: Directory to search recursively for datasets.
            pattern: Glob pattern identifying dataset files. Defaults to
                :data:`CSV_DISCOVERY_PATTERN`.
        """
        self._search_root = search_root
        self._pattern = pattern

    def discover(self) -> list[Path]:
        """Recursively find every matching dataset file under the search root.

        Returns:
            A sorted list of absolute file paths. Returns an empty list
            (rather than raising) if the search root does not exist, since
            an empty ingestion directory is a valid, non-critical state.

        Raises:
            NotADirectoryError: If ``search_root`` exists but is not a
                directory.
        """
        if not self._search_root.exists():
            logger.warning(
                "Discovery root '%s' does not exist; no datasets to process.",
                self._search_root,
            )
            return []

        if not self._search_root.is_dir():
            raise NotADirectoryError(
                f"Discovery root is not a directory: {self._search_root}"
            )

        discovered = sorted(self._search_root.rglob(self._pattern))
        logger.info(
            "Discovered %d dataset(s) under '%s' matching '%s'.",
            len(discovered),
            self._search_root,
            self._pattern,
        )
        return discovered


# --------------------------------------------------------------------------- #
# Per-dataset processing
# --------------------------------------------------------------------------- #


class DatasetProcessor:
    """Runs a single dataset through load -> profile -> quality-check -> report.

    Every dependency is injected, so this class has zero knowledge of how
    files are discovered or how results are aggregated — it only knows how
    to fully process exactly one file, and to fail safely if any stage
    raises.
    """

    def __init__(
        self,
        file_loader: FileLoader,
        profiler: DataProfiler,
        quality_checker: DataQualityChecker,
        report_generator: ReportGenerator,
    ) -> None:
        """Initialize the processor with its collaborators.

        Args:
            file_loader: Loads raw files into DataFrames.
            profiler: Computes structural/statistical profiles.
            quality_checker: Computes quality scores and warnings.
            report_generator: Persists per-dataset report artifacts.
        """
        self._file_loader = file_loader
        self._profiler = profiler
        self._quality_checker = quality_checker
        self._report_generator = report_generator

    def process(self, file_path: Path) -> DatasetExecutionResult:
        """Process a single dataset file end-to-end.

        Any exception raised at any stage is caught, logged, and converted
        into a failed :class:`DatasetExecutionResult` so that the caller can
        continue processing the remaining datasets.

        Args:
            file_path: Absolute path to the CSV file to process.

        Returns:
            A :class:`DatasetExecutionResult` describing the outcome.
        """
        dataset_name = file_path.stem
        started_at = time.perf_counter()

        try:
            logger.info("Processing dataset '%s' (%s).", dataset_name, file_path)

            dataframe = self._file_loader.load_csv(file_path)
            profile = self._profiler.profile_dataframe(
                dataframe, dataset_name=dataset_name
            )
            quality_report = self._quality_checker.check_quality(
                dataframe, dataset_name=dataset_name
            )
            report_paths = self._report_generator.generate_full_report(
                dataset_name=dataset_name,
                profile=profile,
                quality_report=quality_report,
            )

            duration = time.perf_counter() - started_at
            logger.info(
                "Successfully processed dataset '%s' in %.2fs.",
                dataset_name,
                duration,
            )

            return DatasetExecutionResult(
                dataset_name=dataset_name,
                file_path=file_path,
                status=DatasetStatus.SUCCESS,
                duration_seconds=duration,
                row_count=profile.get("overview", {}).get("row_count"),
                column_count=profile.get("overview", {}).get("column_count"),
                overall_quality_score=quality_report.get("scores", {}).get(
                    "overall_score"
                ),
                warning_count=quality_report.get("warning_count"),
                report_paths=report_paths,
            )

        except FileLoaderError as exc:
            return self._build_failure_result(
                dataset_name, file_path, started_at, f"File loading failed: {exc}"
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-dataset failures
            logger.exception("Unexpected error while processing '%s'.", dataset_name)
            return self._build_failure_result(
                dataset_name, file_path, started_at, f"Unexpected error: {exc}"
            )

    @staticmethod
    def _build_failure_result(
        dataset_name: str, file_path: Path, started_at: float, message: str
    ) -> DatasetExecutionResult:
        """Build a failed :class:`DatasetExecutionResult`.

        Args:
            dataset_name: The dataset identifier.
            file_path: The source file path.
            started_at: The ``time.perf_counter()`` value recorded at the
                start of processing, used to compute elapsed duration.
            message: A human-readable description of the failure.

        Returns:
            A :class:`DatasetExecutionResult` with ``status=FAILURE``.
        """
        duration = time.perf_counter() - started_at
        logger.error(
            "Failed to process dataset '%s' after %.2fs: %s",
            dataset_name,
            duration,
            message,
        )
        return DatasetExecutionResult(
            dataset_name=dataset_name,
            file_path=file_path,
            status=DatasetStatus.FAILURE,
            duration_seconds=duration,
            error_message=message,
        )


# --------------------------------------------------------------------------- #
# Master summary
# --------------------------------------------------------------------------- #


class ExecutionSummaryBuilder:
    """Aggregates per-dataset results into a persisted master summary report."""

    def __init__(self, paths: ProjectPaths) -> None:
        """Initialize the builder.

        Args:
            paths: Resolved project path configuration, used to locate the
                output directory for the master summary.
        """
        self._paths = paths

    def build_and_persist(
        self,
        results: list[DatasetExecutionResult],
        *,
        pipeline_started_at: datetime,
        pipeline_duration_seconds: float,
    ) -> Path:
        """Build the master summary payload and write it to disk as JSON.

        Args:
            results: Every per-dataset execution result, success or failure.
            pipeline_started_at: Timestamp marking the start of the run.
            pipeline_duration_seconds: Total wall-clock duration of the run.

        Returns:
            The absolute path to the written master summary JSON file.
        """
        import json

        successes = [r for r in results if r.succeeded]
        failures = [r for r in results if not r.succeeded]

        summary: dict[str, Any] = {
            "pipeline": "AI Procurement Intelligence Platform - Execution Pipeline",
            "run_started_at": pipeline_started_at.isoformat(),
            "run_duration_seconds": round(pipeline_duration_seconds, 4),
            "datasets_discovered": len(results),
            "datasets_succeeded": len(successes),
            "datasets_failed": len(failures),
            "results": [
                {
                    "dataset_name": r.dataset_name,
                    "file_path": str(r.file_path),
                    "status": r.status.name,
                    "duration_seconds": round(r.duration_seconds, 4),
                    "row_count": r.row_count,
                    "column_count": r.column_count,
                    "overall_quality_score": r.overall_quality_score,
                    "warning_count": r.warning_count,
                    "report_paths": {
                        k: str(v) for k, v in r.report_paths.items()
                    },
                    "error_message": r.error_message,
                }
                for r in results
            ],
        }

        self._paths.profiling_reports_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime(SUMMARY_TIMESTAMP_FORMAT)
        output_path = (
            self._paths.profiling_reports_dir / f"master_summary_{timestamp}.json"
        )

        with output_path.open("w", encoding="utf-8") as summary_file:
            json.dump(summary, summary_file, indent=2, default=str)

        logger.info("Master summary report written to '%s'.", output_path)
        return output_path


# --------------------------------------------------------------------------- #
# Console reporting
# --------------------------------------------------------------------------- #


class ConsoleReporter:
    """Renders professional progress updates and final statistics to stdout.

    Kept entirely separate from logging: logging targets operators/log
    aggregation, while this class targets a human watching the terminal.
    """

    def print_header(self, dataset_count: int, discovery_root: Path) -> None:
        """Print the pipeline start banner.

        Args:
            dataset_count: Number of datasets discovered.
            discovery_root: The directory that was searched.
        """
        self._print_rule()
        print("AI PROCUREMENT INTELLIGENCE PLATFORM — EXECUTION PIPELINE")
        self._print_rule()
        print(f"Discovery root : {discovery_root}")
        print(f"Datasets found : {dataset_count}")
        self._print_rule()

    def print_progress(
        self, index: int, total: int, result: DatasetExecutionResult
    ) -> None:
        """Print a single-line progress update for one processed dataset.

        Args:
            index: 1-based position of this dataset in the run.
            total: Total number of datasets being processed.
            result: The outcome of processing this dataset.
        """
        status_label = "OK  " if result.succeeded else "FAIL"
        if result.succeeded:
            detail = (
                f"rows={result.row_count} cols={result.column_count} "
                f"quality={result.overall_quality_score} "
                f"warnings={result.warning_count}"
            )
        else:
            detail = f"error={result.error_message}"

        print(
            f"[{index:>3}/{total:<3}] [{status_label}] "
            f"{result.dataset_name:<30} ({result.duration_seconds:.2f}s)  {detail}"
        )

    def print_summary(
        self,
        results: list[DatasetExecutionResult],
        *,
        total_duration_seconds: float,
        master_summary_path: Path | None,
    ) -> None:
        """Print final execution statistics.

        Args:
            results: Every per-dataset execution result.
            total_duration_seconds: Total wall-clock duration of the run.
            master_summary_path: Path to the persisted master summary, or
                ``None`` if it could not be written.
        """
        successes = [r for r in results if r.succeeded]
        failures = [r for r in results if not r.succeeded]

        self._print_rule()
        print("EXECUTION SUMMARY")
        self._print_rule()
        print(f"Total datasets   : {len(results)}")
        print(f"Succeeded        : {len(successes)}")
        print(f"Failed           : {len(failures)}")
        print(f"Total duration   : {total_duration_seconds:.2f}s")
        if master_summary_path is not None:
            print(f"Master summary   : {master_summary_path}")

        if failures:
            print()
            print("FAILURES:")
            for result in failures:
                print(f"  - {result.dataset_name}: {result.error_message}")

        self._print_rule()

    @staticmethod
    def _print_rule() -> None:
        """Print a horizontal divider line."""
        print("=" * CONSOLE_RULE_WIDTH)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


class ExecutionPipeline:
    """Coordinates discovery, processing, summarization, and reporting.

    This class contains no business logic itself — it wires together its
    injected collaborators and drives the overall control flow, which keeps
    it trivially testable (each collaborator can be mocked independently).
    """

    def __init__(
        self,
        discovery_service: DatasetDiscoveryService,
        processor: DatasetProcessor,
        summary_builder: ExecutionSummaryBuilder,
        console_reporter: ConsoleReporter,
        discovery_root: Path,
    ) -> None:
        """Initialize the pipeline with its collaborators.

        Args:
            discovery_service: Finds dataset files to process.
            processor: Processes a single dataset end-to-end.
            summary_builder: Aggregates and persists the master summary.
            console_reporter: Renders console progress and statistics.
            discovery_root: The directory that was searched, for display
                purposes only.
        """
        self._discovery_service = discovery_service
        self._processor = processor
        self._summary_builder = summary_builder
        self._console_reporter = console_reporter
        self._discovery_root = discovery_root

    def run(self) -> ExitCode:
        """Execute the full pipeline: discover, process, summarize, report.

        Returns:
            The :class:`ExitCode` reflecting the outcome of the run.
        """
        pipeline_started_at = datetime.now()
        pipeline_start_counter = time.perf_counter()

        logger.info("Execution pipeline starting.")

        try:
            dataset_files = self._discovery_service.discover()
        except Exception:  # noqa: BLE001 - discovery failure is critical
            logger.exception("Critical failure during dataset discovery.")
            return ExitCode.CRITICAL_FAILURE

        self._console_reporter.print_header(len(dataset_files), self._discovery_root)

        if not dataset_files:
            logger.warning("No datasets discovered; nothing to process.")
            total_duration = time.perf_counter() - pipeline_start_counter
            self._console_reporter.print_summary(
                [], total_duration_seconds=total_duration, master_summary_path=None
            )
            return ExitCode.COMPLETED_WITH_FAILURES

        results: list[DatasetExecutionResult] = []
        for index, file_path in enumerate(dataset_files, start=1):
            result = self._processor.process(file_path)
            results.append(result)
            self._console_reporter.print_progress(index, len(dataset_files), result)

        total_duration = time.perf_counter() - pipeline_start_counter

        master_summary_path: Path | None = None
        try:
            master_summary_path = self._summary_builder.build_and_persist(
                results,
                pipeline_started_at=pipeline_started_at,
                pipeline_duration_seconds=total_duration,
            )
        except Exception:  # noqa: BLE001 - summary persistence must not crash the run
            logger.exception("Failed to persist master summary report.")

        self._console_reporter.print_summary(
            results,
            total_duration_seconds=total_duration,
            master_summary_path=master_summary_path,
        )

        failure_count = sum(1 for r in results if not r.succeeded)
        if failure_count > 0:
            logger.warning(
                "Execution pipeline finished with %d failure(s) out of %d dataset(s).",
                failure_count,
                len(results),
            )
            return ExitCode.COMPLETED_WITH_FAILURES

        logger.info("Execution pipeline finished successfully: all datasets processed.")
        return ExitCode.SUCCESS


# --------------------------------------------------------------------------- #
# Composition root
# --------------------------------------------------------------------------- #


def build_pipeline() -> ExecutionPipeline:
    """Compose the execution pipeline and its collaborators.

    This is the sole place in the application where concrete
    implementations are wired together, keeping every other class ignorant
    of how its dependencies are constructed (Dependency Inversion).

    Returns:
        A fully configured :class:`ExecutionPipeline`, ready to run.
    """
    paths = get_project_paths()
    discovery_root = paths.raw_data_dir / RAW_DATA_DISCOVERY_SUBDIR

    discovery_service = DatasetDiscoveryService(discovery_root)
    processor = DatasetProcessor(
        file_loader=FileLoader(),
        profiler=DataProfiler(),
        quality_checker=DataQualityChecker(),
        report_generator=ReportGenerator(),
    )
    summary_builder = ExecutionSummaryBuilder(paths)
    console_reporter = ConsoleReporter()

    return ExecutionPipeline(
        discovery_service=discovery_service,
        processor=processor,
        summary_builder=summary_builder,
        console_reporter=console_reporter,
        discovery_root=discovery_root,
    )


def main() -> int:
    """Application entry point.

    Returns:
        The integer process exit code (see :class:`ExitCode`).
    """
    try:
        pipeline = build_pipeline()
        exit_code = pipeline.run()
    except Exception:  # noqa: BLE001 - last-resort safety net at the entry point
        logger.exception("Critical, unhandled failure in the execution pipeline.")
        return int(ExitCode.CRITICAL_FAILURE)

    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())