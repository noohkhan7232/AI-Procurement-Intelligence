"""Phase 3 — Enterprise ETL Pipeline: load ``data/interim/`` into SQLite.

:class:`ETLPipeline` discovers every cleaned CSV under ``data/interim/``
(recursively, reusing :class:`src.main.DatasetDiscoveryService` — the same
discovery service already used by the profiling and cleaning pipelines, so
discovery logic exists in exactly one place), derives a SQLite schema for
each dataset via :class:`~src.database.schema_builder.SchemaBuilder`,
infers foreign key relationships across the whole discovered set, loads
tables in dependency order (parents before children) so foreign key
constraints are always satisfiable at insert time, and validates the result
via :class:`~src.database.database_validator.DatabaseValidator`.

Each dataset loads independently: a failure while loading one dataset is
isolated (its transaction rolls back, leaving no partial rows) and logged,
and the pipeline continues with the remaining datasets, reporting every
failure in the final Execution Report.

Usage:
    $ python -m src.database.etl_pipeline
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

from src.config.database_config import DatabaseConfig, get_database_config
from src.config.paths import ProjectPaths, get_project_paths
from src.database.connection import DatabaseConnectionManager
from src.database.data_loader import DataLoader, DataLoadError
from src.database.database_manager import DatabaseManager
from src.database.database_validator import DatabaseValidator
from src.database.schema_builder import SchemaBuilder, TableSchema
from src.database.sql_executor import SQLExecutor
from src.database.transaction_manager import TransactionManager
from src.application.dataset_discovery.service import DatasetDiscoveryService
from src.utils.file_loader import FileLoader, FileLoaderError
from src.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Glob pattern used for recursive CSV discovery under ``data/interim/``.
CSV_DISCOVERY_PATTERN: str = "*.csv"

#: Timestamp format used to name each immutable report run folder.
RUN_TIMESTAMP_FORMAT: str = "%Y%m%d_%H%M%S"

#: JSON serialization indentation width.
JSON_INDENT: int = 2

#: Console divider width for section headers.
CONSOLE_RULE_WIDTH: int = 60


class ETLExitCode(IntEnum):
    """Process exit codes returned by :func:`main`."""

    SUCCESS = 0
    COMPLETED_WITH_FAILURES = 1
    CRITICAL_FAILURE = 2


class DatasetLoadStatus(IntEnum):
    """Outcome of loading a single dataset into the database."""

    SUCCESS = 0
    FAILURE = 1


# --------------------------------------------------------------------------- #
# Result containers
# --------------------------------------------------------------------------- #


@dataclass
class DatasetLoadRecord:
    """The complete outcome of loading a single dataset into the database.

    Attributes:
        dataset_name: Human-readable dataset identifier (the file's stem),
            also used as the resulting table name.
        source_path: Absolute path to the source CSV under ``data/interim``.
        status: Whether the dataset succeeded or failed.
        duration_seconds: Wall-clock time spent on this dataset.
        rows_expected: Row count in the source DataFrame, if it was loaded.
        rows_loaded: Row count actually present in the table afterward, if
            the load succeeded.
        error_message: Description of the failure, if any.
    """

    dataset_name: str
    source_path: Path
    status: DatasetLoadStatus
    duration_seconds: float
    rows_expected: int | None = None
    rows_loaded: int | None = None
    error_message: str | None = None

    @property
    def succeeded(self) -> bool:
        """Whether this dataset was loaded without error."""
        return self.status is DatasetLoadStatus.SUCCESS


# --------------------------------------------------------------------------- #
# Dependency ordering
# --------------------------------------------------------------------------- #


class TableLoadOrderResolver:
    """Topologically orders tables so parents load before their children.

    A table ``A`` depends on table ``B`` when ``A`` declares a foreign key
    referencing ``B``. Loading in dependency order guarantees every foreign
    key value inserted into a child table already has a matching row in its
    parent table, so ``PRAGMA foreign_keys = ON`` never rejects a valid load
    purely due to insertion order.
    """

    def resolve(self, schemas: dict[str, TableSchema]) -> list[str]:
        """Compute a load order where every table's dependencies come first.

        Uses Kahn's algorithm for topological sorting. If the dependency
        graph contains a cycle (which well-formed procurement data should
        never produce), the cyclic tables are appended in their original
        discovery order and a warning is logged, rather than failing the
        entire pipeline.

        Args:
            schemas: Every table's schema, keyed by table name, with
                foreign keys already inferred.

        Returns:
            Table names ordered so that every table referenced by another
            table's foreign key appears before that table.
        """
        dependencies: dict[str, set[str]] = {
            table_name: {
                fk.referenced_table
                for fk in schema.foreign_keys
                if fk.referenced_table in schemas
            }
            for table_name, schema in schemas.items()
        }

        ordered: list[str] = []
        remaining = set(schemas.keys())

        while remaining:
            ready = sorted(
                table_name
                for table_name in remaining
                if dependencies[table_name] <= set(ordered)
            )
            if not ready:
                logger.warning(
                    "Circular or unresolved table dependency detected among "
                    "%s; appending in discovery order.",
                    sorted(remaining),
                )
                ordered.extend(sorted(remaining))
                break

            ordered.extend(ready)
            remaining -= set(ready)

        return ordered


# --------------------------------------------------------------------------- #
# Report persistence (versioned: runs/<timestamp>/ + latest/)
# --------------------------------------------------------------------------- #


class ETLReportWriter:
    """Persists the five required Phase 3 reports with immutable run versioning.

    Mirrors the versioning model used throughout the platform: every report
    is written into this instance's dedicated run folder under
    ``reports/database/runs/`` (never overwritten by later runs) and
    mirrored into ``reports/database/latest/`` (cleared once, at
    construction time).
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
        self.run_dir = paths.database_runs_dir / self.run_timestamp
        self.latest_dir = paths.database_latest_dir

        self.run_dir.mkdir(parents=True, exist_ok=True)
        self._reset_latest_directory()

    def write_reports(
        self,
        *,
        database_config: DatabaseConfig,
        schemas: dict[str, TableSchema],
        records: list[DatasetLoadRecord],
        database_manager: DatabaseManager,
        validation_report: dict[str, Any],
        started_at: datetime,
        total_duration_seconds: float,
    ) -> dict[str, Path]:
        """Write all five required reports for this ETL run.

        Args:
            database_config: The active database configuration.
            schemas: Every successfully materialized table's schema.
            records: Every per-dataset load record, success or failure.
            database_manager: Used to read final row counts for the Table
                Summary report.
            validation_report: Output of
                :meth:`~src.database.database_validator.DatabaseValidator.validate_all`.
            started_at: Timestamp marking the start of the run.
            total_duration_seconds: Total wall-clock duration of the run.

        Returns:
            A mapping of report type to the absolute path of the written
            file inside this run's immutable directory.
        """
        successes = [r for r in records if r.succeeded]
        failures = [r for r in records if not r.succeeded]

        return {
            "database_summary": self._write_json(
                "database_summary.json",
                self._build_database_summary(
                    database_config, schemas, successes, failures, started_at
                ),
            ),
            "table_summary": self._write_json(
                "table_summary.json",
                self._build_table_summary(schemas, database_manager),
            ),
            "load_summary": self._write_json(
                "load_summary.json", self._build_load_summary(records)
            ),
            "validation_report": self._write_json(
                "validation_report.json", validation_report
            ),
            "execution_report": self._write_json(
                "execution_report.json",
                self._build_execution_report(
                    records, started_at, total_duration_seconds
                ),
            ),
        }

    # ------------------------------------------------------------------ #
    # Report builders
    # ------------------------------------------------------------------ #

    def _build_database_summary(
        self,
        database_config: DatabaseConfig,
        schemas: dict[str, TableSchema],
        successes: list[DatasetLoadRecord],
        failures: list[DatasetLoadRecord],
        started_at: datetime,
    ) -> dict[str, Any]:
        """Build the Database Summary payload."""
        database_size_bytes = (
            database_config.database_path.stat().st_size
            if database_config.database_path.exists()
            else 0
        )
        total_foreign_keys = sum(len(schema.foreign_keys) for schema in schemas.values())

        return {
            "run_timestamp": self.run_timestamp,
            "run_started_at": started_at.isoformat(),
            "database_path": str(database_config.database_path),
            "database_size_bytes": database_size_bytes,
            "tables_created": len(successes),
            "tables_failed": len(failures),
            "total_foreign_keys": total_foreign_keys,
            "total_rows_loaded": sum(r.rows_loaded or 0 for r in successes),
        }

    def _build_table_summary(
        self, schemas: dict[str, TableSchema], database_manager: DatabaseManager
    ) -> dict[str, Any]:
        """Build the Table Summary payload (structure of every loaded table)."""
        return {
            "run_timestamp": self.run_timestamp,
            "tables": {
                table_name: {
                    "columns": [
                        {
                            "name": column.name,
                            "sql_type": column.sql_type,
                            "is_nullable": column.is_nullable,
                            "is_primary_key": column.is_primary_key,
                        }
                        for column in schema.columns
                    ],
                    "primary_key": schema.primary_key,
                    "foreign_keys": [
                        {
                            "column": fk.column,
                            "referenced_table": fk.referenced_table,
                            "referenced_column": fk.referenced_column,
                        }
                        for fk in schema.foreign_keys
                    ],
                    "row_count": database_manager.get_row_count(table_name),
                }
                for table_name, schema in schemas.items()
            },
        }

    def _build_load_summary(self, records: list[DatasetLoadRecord]) -> dict[str, Any]:
        """Build the Load Summary payload (per-dataset load outcomes)."""
        return {
            "run_timestamp": self.run_timestamp,
            "datasets": [
                {
                    "dataset_name": r.dataset_name,
                    "source_path": str(r.source_path),
                    "status": r.status.name,
                    "duration_seconds": round(r.duration_seconds, 4),
                    "rows_expected": r.rows_expected,
                    "rows_loaded": r.rows_loaded,
                    "error_message": r.error_message,
                }
                for r in records
            ],
        }

    def _build_execution_report(
        self,
        records: list[DatasetLoadRecord],
        started_at: datetime,
        total_duration_seconds: float,
    ) -> dict[str, Any]:
        """Build the Execution Report payload (overall pipeline statistics)."""
        successes = [r for r in records if r.succeeded]
        failures = [r for r in records if not r.succeeded]

        return {
            "pipeline": "AI Procurement Intelligence Platform - ETL Pipeline",
            "run_timestamp": self.run_timestamp,
            "run_started_at": started_at.isoformat(),
            "total_duration_seconds": round(total_duration_seconds, 4),
            "datasets_discovered": len(records),
            "datasets_succeeded": len(successes),
            "datasets_failed": len(failures),
            "failures": [
                {"dataset_name": r.dataset_name, "error_message": r.error_message}
                for r in failures
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


class ETLConsoleReporter:
    """Renders professional progress updates and final statistics to stdout."""

    def print_header(self, dataset_count: int, discovery_root: Path) -> None:
        """Print the pipeline start banner."""
        self._print_rule()
        print("AI PROCUREMENT INTELLIGENCE PLATFORM — ETL PIPELINE")
        self._print_rule()
        print(f"Discovery root : {discovery_root}")
        print(f"Datasets found : {dataset_count}")
        self._print_rule()

    def print_progress(self, index: int, total: int, record: DatasetLoadRecord) -> None:
        """Print a single-line progress update for one loaded dataset."""
        status_label = "OK  " if record.succeeded else "FAIL"
        if record.succeeded:
            detail = f"rows={record.rows_loaded}/{record.rows_expected}"
        else:
            detail = f"error={record.error_message}"

        print(
            f"[{index:>3}/{total:<3}] [{status_label}] "
            f"{record.dataset_name:<30} ({record.duration_seconds:.2f}s)  {detail}"
        )

    def print_summary(
        self,
        records: list[DatasetLoadRecord],
        *,
        total_duration_seconds: float,
        validation_report: dict[str, Any] | None,
        report_paths: dict[str, Path] | None,
    ) -> None:
        """Print final execution statistics and validation outcome."""
        successes = [r for r in records if r.succeeded]
        failures = [r for r in records if not r.succeeded]

        self._print_rule()
        print("ETL EXECUTION SUMMARY")
        self._print_rule()
        print(f"Total datasets   : {len(records)}")
        print(f"Succeeded        : {len(successes)}")
        print(f"Failed           : {len(failures)}")
        print(f"Total duration   : {total_duration_seconds:.2f}s")
        if validation_report is not None:
            print(f"Validation       : overall_valid={validation_report['overall_valid']}")
        if report_paths is not None:
            for report_type, path in report_paths.items():
                print(f"{report_type:<20} : {path}")

        if failures:
            print()
            print("FAILURES:")
            for record in failures:
                print(f"  - {record.dataset_name}: {record.error_message}")

        self._print_rule()

    @staticmethod
    def _print_rule() -> None:
        """Print a horizontal divider line."""
        print("=" * CONSOLE_RULE_WIDTH)


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


class ETLPipeline:
    """Coordinates schema derivation, dependency-ordered loading, validation,
    and reporting for the entire ``data/interim/`` -> SQLite ETL process.

    Every collaborator is injected via the constructor (Dependency
    Inversion), so the pipeline can be tested against an in-memory database,
    a custom discovery root, or a fake file loader without any change to
    its orchestration logic.
    """

    def __init__(
        self,
        *,
        database_config: DatabaseConfig | None = None,
        file_loader: FileLoader | None = None,
        schema_builder: SchemaBuilder | None = None,
        discovery_root: Path | None = None,
        run_timestamp: str | None = None,
    ) -> None:
        """Initialize the ETL pipeline and its collaborators.

        Args:
            database_config: The database configuration to connect with. If
                ``None``, resolved via
                :func:`src.config.database_config.get_database_config`.
            file_loader: Loads raw CSV files. If ``None``, a default
                :class:`~src.utils.file_loader.FileLoader` is created.
            schema_builder: Derives table schemas. If ``None``, a default
                :class:`~src.database.schema_builder.SchemaBuilder` is created.
            discovery_root: Directory to recursively search for cleaned CSV
                datasets. If ``None``, defaults to ``<interim_data_dir>``.
            run_timestamp: Explicit run identifier, primarily for
                deterministic testing. If ``None``, derived from the
                current time.
        """
        self._paths = get_project_paths()
        self._database_config = database_config or get_database_config()
        self._discovery_root = discovery_root or self._paths.interim_data_dir
        self._discovery_service = DatasetDiscoveryService(
            self._discovery_root, pattern=CSV_DISCOVERY_PATTERN
        )
        self._file_loader = file_loader or FileLoader()
        self._schema_builder = schema_builder or SchemaBuilder()
        self._order_resolver = TableLoadOrderResolver()
        self._connection_manager = DatabaseConnectionManager(self._database_config)
        self._report_writer = ETLReportWriter(self._paths, run_timestamp=run_timestamp)
        self._console = ETLConsoleReporter()

    def run(self) -> ETLExitCode:
        """Execute the full ETL pipeline end-to-end.

        Returns:
            The :class:`ETLExitCode` reflecting the outcome of the run.
        """
        started_at = datetime.now()
        start_counter = time.perf_counter()

        logger.info("ETL pipeline starting.")

        try:
            dataset_files = self._discovery_service.discover()
        except Exception:  # noqa: BLE001 - discovery failure is critical
            logger.exception("Critical failure during dataset discovery.")
            return ETLExitCode.CRITICAL_FAILURE

        self._console.print_header(len(dataset_files), self._discovery_root)

        if not dataset_files:
            logger.warning("No datasets discovered under '%s'.", self._discovery_root)
            total_duration = time.perf_counter() - start_counter
            self._console.print_summary(
                [],
                total_duration_seconds=total_duration,
                validation_report=None,
                report_paths=None,
            )
            return ETLExitCode.COMPLETED_WITH_FAILURES

        try:
            with self._connection_manager.connect() as connection:
                exit_code = self._run_with_connection(
                    connection, dataset_files, started_at, start_counter
                )
        except Exception:  # noqa: BLE001 - last-resort safety net around the DB session
            logger.exception("Critical failure during ETL execution.")
            return ETLExitCode.CRITICAL_FAILURE

        return exit_code

    def _run_with_connection(
        self,
        connection: Any,
        dataset_files: list[Path],
        started_at: datetime,
        start_counter: float,
    ) -> ETLExitCode:
        """Run schema derivation, loading, validation, and reporting on an open connection.

        Args:
            connection: An open, configured ``sqlite3.Connection``.
            dataset_files: Every discovered CSV file to load.
            started_at: Timestamp marking the start of the run.
            start_counter: ``time.perf_counter()`` value at the start of the run.

        Returns:
            The :class:`ETLExitCode` reflecting the outcome of the run.
        """
        sql_executor = SQLExecutor(connection)
        database_manager = DatabaseManager(
            connection, schema_builder=self._schema_builder, sql_executor=sql_executor
        )
        data_loader = DataLoader(sql_executor, batch_size=self._database_config.batch_insert_size)

        dataframes, build_failures = self._load_dataframes(dataset_files)
        schemas = self._build_schemas(dataframes)
        self._schema_builder.infer_foreign_keys(schemas)
        load_order = self._order_resolver.resolve(schemas)

        # Drop any pre-existing versions of these tables in REVERSE
        # dependency order (children before parents) before creating or
        # loading anything. With PRAGMA foreign_keys = ON, DROP TABLE
        # performs an implicit delete that SQLite validates against other
        # tables' foreign keys, so a parent from a previous run cannot be
        # dropped while an old child row still references it.
        for table_name in reversed(load_order):
            try:
                database_manager.drop_table(table_name)
            except Exception:  # noqa: BLE001 - a stale table is not fatal to the run
                logger.exception(
                    "Failed to drop pre-existing table '%s'; it will be "
                    "recreated with DROP TABLE IF EXISTS during load.",
                    table_name,
                )

        records: list[DatasetLoadRecord] = list(build_failures)
        for index, table_name in enumerate(load_order, start=1):
            record = self._load_one_table(
                table_name,
                dataframes[table_name],
                schemas[table_name],
                database_manager,
                data_loader,
                connection,
            )
            records.append(record)
            self._console.print_progress(index, len(load_order), record)

        successfully_loaded_schemas = {
            name: schema
            for name, schema in schemas.items()
            if any(r.dataset_name == name and r.succeeded for r in records)
        }
        database_validator = DatabaseValidator(sql_executor)
        validation_report = database_validator.validate_all(successfully_loaded_schemas)

        total_duration = time.perf_counter() - start_counter

        report_paths: dict[str, Path] | None = None
        try:
            report_paths = self._report_writer.write_reports(
                database_config=self._database_config,
                schemas=successfully_loaded_schemas,
                records=records,
                database_manager=database_manager,
                validation_report=validation_report,
                started_at=started_at,
                total_duration_seconds=total_duration,
            )
        except Exception:  # noqa: BLE001 - report persistence must not crash the run
            logger.exception("Failed to persist ETL reports.")

        self._console.print_summary(
            records,
            total_duration_seconds=total_duration,
            validation_report=validation_report,
            report_paths=report_paths,
        )

        failure_count = sum(1 for r in records if not r.succeeded)
        if failure_count > 0 or not validation_report["overall_valid"]:
            logger.warning(
                "ETL pipeline finished with %d failure(s) and overall_valid=%s.",
                failure_count,
                validation_report["overall_valid"],
            )
            return ETLExitCode.COMPLETED_WITH_FAILURES

        logger.info("ETL pipeline finished successfully: all datasets loaded and valid.")
        return ETLExitCode.SUCCESS

    # ------------------------------------------------------------------ #
    # Pipeline stages
    # ------------------------------------------------------------------ #

    def _load_dataframes(
        self, dataset_files: list[Path]
    ) -> tuple[dict[str, Any], list[DatasetLoadRecord]]:
        """Load every discovered CSV into memory as a DataFrame.

        Datasets that fail to load (missing, empty, unparsable) are
        isolated here so schema derivation and table loading only ever see
        datasets that loaded successfully.

        Args:
            dataset_files: Every discovered CSV file path.

        Returns:
            A tuple of ``(dataframes_by_table_name, failure_records)``.
        """
        dataframes: dict[str, Any] = {}
        failures: list[DatasetLoadRecord] = []

        for file_path in dataset_files:
            table_name = file_path.stem
            started = time.perf_counter()
            try:
                dataframes[table_name] = self._file_loader.load_csv(file_path)
            except FileLoaderError as exc:
                failures.append(
                    DatasetLoadRecord(
                        dataset_name=table_name,
                        source_path=file_path,
                        status=DatasetLoadStatus.FAILURE,
                        duration_seconds=time.perf_counter() - started,
                        error_message=f"File loading failed: {exc}",
                    )
                )
                logger.error("Failed to load '%s': %s", table_name, exc)

        return dataframes, failures

    def _build_schemas(self, dataframes: dict[str, Any]) -> dict[str, TableSchema]:
        """Derive a schema for every successfully loaded dataset.

        Args:
            dataframes: Every successfully loaded dataset, keyed by table name.

        Returns:
            Every derived schema, keyed by table name (foreign keys not yet
            inferred at this point).
        """
        return {
            table_name: self._schema_builder.build_schema(dataframe, table_name=table_name)
            for table_name, dataframe in dataframes.items()
        }

    def _load_one_table(
        self,
        table_name: str,
        dataframe: Any,
        schema: TableSchema,
        database_manager: DatabaseManager,
        data_loader: DataLoader,
        connection: Any,
    ) -> DatasetLoadRecord:
        """Materialize and load a single table, isolating any failure.

        Args:
            table_name: The dataset/table name.
            dataframe: The dataset's already-loaded DataFrame.
            schema: The table's derived schema.
            database_manager: Used to create the table and its indexes.
            data_loader: Used to insert the dataframe's rows.
            connection: The active database connection, used to open this
                table's own transaction for the data load.

        Returns:
            A :class:`DatasetLoadRecord` describing the outcome. Any
            exception raised during table creation or loading is caught,
            logged, and converted into a failure record so the caller can
            continue with the remaining tables.
        """
        started = time.perf_counter()
        source_path = self._discovery_root / f"{table_name}.csv"
        rows_expected = dataframe.shape[0]

        try:
            database_manager.create_table(schema, drop_existing=False)

            load_summary = data_loader.load(
                dataframe, schema, TransactionManager(connection)
            )

            duration = time.perf_counter() - started
            return DatasetLoadRecord(
                dataset_name=table_name,
                source_path=source_path,
                status=DatasetLoadStatus.SUCCESS,
                duration_seconds=duration,
                rows_expected=rows_expected,
                rows_loaded=load_summary["rows_loaded"],
            )

        except DataLoadError as exc:
            return self._build_failure_record(
                table_name, source_path, started, rows_expected, str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-dataset failures
            logger.exception("Unexpected error while loading table '%s'.", table_name)
            return self._build_failure_record(
                table_name, source_path, started, rows_expected, f"Unexpected error: {exc}"
            )

    @staticmethod
    def _build_failure_record(
        dataset_name: str,
        source_path: Path,
        started_at: float,
        rows_expected: int,
        message: str,
    ) -> DatasetLoadRecord:
        """Build a failed :class:`DatasetLoadRecord`.

        Args:
            dataset_name: The dataset/table name.
            source_path: The source CSV path.
            started_at: The ``time.perf_counter()`` value at the start of
                processing this dataset.
            rows_expected: The row count that was expected to load.
            message: A human-readable description of the failure.

        Returns:
            A :class:`DatasetLoadRecord` with ``status=FAILURE``.
        """
        duration = time.perf_counter() - started_at
        logger.error(
            "Failed to load table '%s' after %.2fs: %s", dataset_name, duration, message
        )
        return DatasetLoadRecord(
            dataset_name=dataset_name,
            source_path=source_path,
            status=DatasetLoadStatus.FAILURE,
            duration_seconds=duration,
            rows_expected=rows_expected,
            error_message=message,
        )


def main() -> int:
    """Application entry point for the standalone ETL pipeline.

    Returns:
        The integer process exit code (see :class:`ETLExitCode`).
    """
    try:
        pipeline = ETLPipeline()
        exit_code = pipeline.run()
    except Exception:  # noqa: BLE001 - last-resort safety net at the entry point
        logger.exception("Critical, unhandled failure in the ETL pipeline.")
        return int(ETLExitCode.CRITICAL_FAILURE)

    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ETLPipeline",
    "ETLExitCode",
    "DatasetLoadStatus",
    "DatasetLoadRecord",
    "TableLoadOrderResolver",
    "ETLReportWriter",
    "ETLConsoleReporter",
    "main",
]
