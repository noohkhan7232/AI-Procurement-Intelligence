"""Phase 3.1 — Enterprise Database Execution Pipeline.

:class:`DatabaseExecutor` is a production-grade orchestrator that
automatically creates ``database/procurement.db`` and loads every cleaned
CSV under ``data/interim/`` into it, using the *existing* database layer
built in Phase 3 — it introduces no new database logic of its own. Every
operation is performed by the already-tested collaborators from
:mod:`src.database`: :class:`~src.database.connection.DatabaseConnectionManager`,
:class:`~src.database.schema_builder.SchemaBuilder`,
:class:`~src.database.database_manager.DatabaseManager`,
:class:`~src.database.data_loader.DataLoader`, and
:class:`~src.database.database_validator.DatabaseValidator`. It also reuses
the dependency-ordering and record-keeping utilities already proven in
:mod:`src.database.etl_pipeline` (:class:`~src.database.etl_pipeline.TableLoadOrderResolver`,
:class:`~src.database.etl_pipeline.DatasetLoadRecord`) rather than
re-implementing them, and :class:`src.main.DatasetDiscoveryService` for
recursive CSV discovery — so discovery, ordering, and load-record logic
each exist in exactly one place across the whole platform.

This module's only responsibility is orchestration: following the
Load Configuration -> Connect -> Create Database -> Build Schema -> Discover
-> Load -> Validate Relationships -> Validate Database -> Generate Reports
-> Close Connection workflow, rendering the platform's console output for
it, and isolating per-dataset failures so the pipeline always continues
with the remaining datasets.

Usage:
    $ python -m src.database.database_executor
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config.database_config import DatabaseConfig, get_database_config
from src.config.paths import ProjectPaths, get_project_paths
from src.database.connection import DatabaseConnectionManager
from src.database.data_loader import DataLoader, DataLoadError
from src.database.database_manager import DatabaseManager
from src.database.database_validator import DatabaseValidator
from src.database.etl_pipeline import (
    DatasetLoadRecord,
    DatasetLoadStatus,
    ETLExitCode,
    TableLoadOrderResolver,
)
from src.database.schema_builder import SchemaBuilder, TableSchema
from src.database.sql_executor import SQLExecutor
from src.database.transaction_manager import TransactionManager
from src.main import DatasetDiscoveryService
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


# --------------------------------------------------------------------------- #
# Per-dataset load execution
# --------------------------------------------------------------------------- #


class DatasetLoadRunner:
    """Materializes one table and loads its rows, isolating any failure.

    Delegates every operation to the existing, already-tested collaborators
    (:class:`~src.database.database_manager.DatabaseManager` for DDL,
    :class:`~src.database.data_loader.DataLoader` for row insertion) — this
    class performs no schema or SQL logic of its own.
    """

    def __init__(
        self,
        *,
        database_manager: DatabaseManager,
        data_loader: DataLoader,
        connection: Any,
        discovery_root: Path,
    ) -> None:
        """Initialize the runner with its collaborators.

        Args:
            database_manager: Materializes tables (DDL).
            data_loader: Inserts rows and validates row counts.
            connection: The active database connection, used to open each
                table's own load transaction.
            discovery_root: The directory datasets were discovered under,
                used only to record each record's source path.
        """
        self._database_manager = database_manager
        self._data_loader = data_loader
        self._connection = connection
        self._discovery_root = discovery_root

    def load(self, table_name: str, dataframe: Any, schema: TableSchema) -> DatasetLoadRecord:
        """Create a table (already pre-dropped) and load its data.

        Args:
            table_name: The dataset/table name.
            dataframe: The dataset's already-loaded DataFrame.
            schema: The table's derived schema.

        Returns:
            A :class:`~src.database.etl_pipeline.DatasetLoadRecord` describing
            the outcome. Any exception is caught, logged, and converted into
            a failure record so the caller can continue with the remaining
            tables.
        """
        started_at = time.perf_counter()
        source_path = self._discovery_root / f"{table_name}.csv"
        rows_expected = dataframe.shape[0]

        try:
            # Tables are pre-dropped in reverse dependency order by the
            # executor before this runs, so drop_existing=False here avoids
            # a redundant DROP TABLE that could otherwise fail against
            # PRAGMA foreign_keys = ON if called out of order.
            self._database_manager.create_table(schema, drop_existing=False)

            load_summary = self._data_loader.load(
                dataframe, schema, TransactionManager(self._connection)
            )

            duration = time.perf_counter() - started_at
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
                table_name, source_path, started_at, rows_expected, str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - isolate per-dataset failures
            logger.exception("Unexpected error while loading table '%s'.", table_name)
            return self._build_failure_record(
                table_name, source_path, started_at, rows_expected, f"Unexpected error: {exc}"
            )

    @staticmethod
    def _build_failure_record(
        dataset_name: str,
        source_path: Path,
        started_at: float,
        rows_expected: int,
        message: str,
    ) -> DatasetLoadRecord:
        """Build a failed :class:`~src.database.etl_pipeline.DatasetLoadRecord`.

        Args:
            dataset_name: The dataset/table name.
            source_path: The source CSV path.
            started_at: The ``time.perf_counter()`` value at the start of
                processing this dataset.
            rows_expected: The row count that was expected to load.
            message: A human-readable description of the failure.

        Returns:
            A load record with ``status=FAILURE``.
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


# --------------------------------------------------------------------------- #
# Report persistence (versioned: runs/<timestamp>/ + latest/)
# --------------------------------------------------------------------------- #


class DatabaseExecutionReportWriter:
    """Persists the Execution Summary, Database Report, Validation Report,
    and Table Statistics reports for one executor run.

    Uses the same immutable run-versioning model as the rest of the
    platform's report generators: every report is written into this
    instance's dedicated run folder under ``reports/database/runs/`` (never
    overwritten by later runs) and mirrored into ``reports/database/latest/``
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
        """Write the four required reports for this run.

        Args:
            database_config: The active database configuration.
            schemas: Every successfully materialized table's schema.
            records: Every per-dataset load record, success or failure.
            database_manager: Used to read final row counts for the Table
                Statistics report.
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
            "execution_summary": self._write_json(
                "execution_summary.json",
                self._build_execution_summary(
                    records, successes, failures, started_at, total_duration_seconds
                ),
            ),
            "database_report": self._write_json(
                "database_report.json",
                self._build_database_report(database_config, schemas, successes, failures),
            ),
            "validation_report": self._write_json(
                "validation_report.json", validation_report
            ),
            "table_statistics": self._write_json(
                "table_statistics.json",
                self._build_table_statistics(schemas, database_manager),
            ),
        }

    # ------------------------------------------------------------------ #
    # Report builders
    # ------------------------------------------------------------------ #

    def _build_execution_summary(
        self,
        records: list[DatasetLoadRecord],
        successes: list[DatasetLoadRecord],
        failures: list[DatasetLoadRecord],
        started_at: datetime,
        total_duration_seconds: float,
    ) -> dict[str, Any]:
        """Build the Execution Summary payload."""
        return {
            "pipeline": "AI Procurement Intelligence Platform - Database Engine",
            "run_timestamp": self.run_timestamp,
            "run_started_at": started_at.isoformat(),
            "total_duration_seconds": round(total_duration_seconds, 4),
            "datasets_discovered": len(records),
            "datasets_succeeded": len(successes),
            "datasets_failed": len(failures),
            "datasets": [
                {
                    "dataset_name": r.dataset_name,
                    "status": r.status.name,
                    "duration_seconds": round(r.duration_seconds, 4),
                    "rows_expected": r.rows_expected,
                    "rows_loaded": r.rows_loaded,
                    "error_message": r.error_message,
                }
                for r in records
            ],
        }

    def _build_database_report(
        self,
        database_config: DatabaseConfig,
        schemas: dict[str, TableSchema],
        successes: list[DatasetLoadRecord],
        failures: list[DatasetLoadRecord],
    ) -> dict[str, Any]:
        """Build the Database Report payload."""
        database_size_bytes = (
            database_config.database_path.stat().st_size
            if database_config.database_path.exists()
            else 0
        )
        total_foreign_keys = sum(len(schema.foreign_keys) for schema in schemas.values())

        return {
            "run_timestamp": self.run_timestamp,
            "database_path": str(database_config.database_path),
            "database_size_bytes": database_size_bytes,
            "tables_created": len(successes),
            "tables_failed": len(failures),
            "total_foreign_keys": total_foreign_keys,
            "total_rows_loaded": sum(r.rows_loaded or 0 for r in successes),
        }

    def _build_table_statistics(
        self, schemas: dict[str, TableSchema], database_manager: DatabaseManager
    ) -> dict[str, Any]:
        """Build the Table Statistics payload (structure + row counts)."""
        return {
            "run_timestamp": self.run_timestamp,
            "tables": {
                table_name: {
                    "column_count": len(schema.columns),
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


class DatabaseExecutorConsoleReporter:
    """Renders the executor's console banner, workflow stages, and footer."""

    def print_banner(self) -> None:
        """Print the fixed platform/engine banner."""
        print("AI PROCUREMENT INTELLIGENCE PLATFORM")
        print("Database Engine")

    def print_discovery(self, dataset_count: int) -> None:
        """Print the number of datasets found.

        Args:
            dataset_count: Number of datasets discovered.
        """
        print(f"Found {dataset_count} datasets")

    def print_stage(self, message: str) -> None:
        """Print a workflow stage marker (e.g. ``'Creating Database...'``).

        Args:
            message: The stage description to print.
        """
        print(message)

    def print_dataset_start(self, dataset_name: str) -> None:
        """Print the start-of-load line for one dataset.

        Args:
            dataset_name: The dataset about to be loaded.
        """
        print(f"Loading {dataset_name}...")

    def print_dataset_result(self, record: DatasetLoadRecord) -> None:
        """Print the outcome line for one loaded dataset.

        Args:
            record: The dataset's load record.
        """
        print("OK" if record.succeeded else f"FAILED: {record.error_message}")

    def print_failures(self, records: list[DatasetLoadRecord]) -> None:
        """Print a summary of every failed dataset, if any.

        Args:
            records: Every per-dataset load record.
        """
        failures = [r for r in records if not r.succeeded]
        if not failures:
            return
        print("Failures:")
        for record in failures:
            print(f"  - {record.dataset_name}: {record.error_message}")

    def print_ready(self) -> None:
        """Print the final, unconditional completion marker."""
        print("Database Ready")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #


class DatabaseExecutor:
    """Enterprise execution pipeline that creates ``procurement.db`` and
    loads every dataset discovered under ``data/interim/`` using the
    existing Phase 3 database layer.

    Workflow: Load Configuration -> Create Connection -> Create Database ->
    Build Schema -> Discover Datasets -> Load Every Dataset -> Validate
    Relationships -> Validate Database -> Generate Reports -> Close
    Connection.

    Every collaborator is injected via the constructor (Dependency
    Inversion), so the executor can be tested with fakes and reconfigured
    without any change to its orchestration logic.
    """

    def __init__(
        self,
        *,
        database_config: DatabaseConfig | None = None,
        file_loader: FileLoader | None = None,
        schema_builder: SchemaBuilder | None = None,
        discovery_root: Path | None = None,
        run_timestamp: str | None = None,
        console_reporter: DatabaseExecutorConsoleReporter | None = None,
    ) -> None:
        """Initialize the database executor and its collaborators.

        Args:
            database_config: The database configuration to connect with. If
                ``None``, resolved via
                :func:`src.config.database_config.get_database_config`
                (Load Configuration step).
            file_loader: Loads raw CSV files. If ``None``, a default
                :class:`~src.utils.file_loader.FileLoader` is created.
            schema_builder: Derives table schemas. If ``None``, a default
                :class:`~src.database.schema_builder.SchemaBuilder` is created.
            discovery_root: Directory to recursively search for cleaned CSV
                datasets. If ``None``, defaults to ``<interim_data_dir>``.
            run_timestamp: Explicit run identifier, primarily for
                deterministic testing. If ``None``, derived from the
                current time.
            console_reporter: Renders console output. If ``None``, a
                default :class:`DatabaseExecutorConsoleReporter` is used.
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
        self._report_writer = DatabaseExecutionReportWriter(
            self._paths, run_timestamp=run_timestamp
        )
        self._console = console_reporter or DatabaseExecutorConsoleReporter()

    def execute(self) -> ETLExitCode:
        """Run the full database execution pipeline end-to-end.

        Returns:
            The :class:`~src.database.etl_pipeline.ETLExitCode` reflecting
            the outcome of the run.
        """
        started_at = datetime.now()
        start_counter = time.perf_counter()

        self._console.print_banner()
        logger.info("Database executor starting.")

        try:
            dataset_files = self._discovery_service.discover()
        except Exception:  # noqa: BLE001 - discovery failure is critical
            logger.exception("Critical failure during dataset discovery.")
            return ETLExitCode.CRITICAL_FAILURE

        self._console.print_discovery(len(dataset_files))

        if not dataset_files:
            logger.warning("No datasets discovered under '%s'.", self._discovery_root)
            self._console.print_ready()
            return ETLExitCode.COMPLETED_WITH_FAILURES

        try:
            with self._connection_manager.connect() as connection:
                exit_code = self._run_with_connection(
                    connection, dataset_files, started_at, start_counter
                )
        except Exception:  # noqa: BLE001 - last-resort safety net around the DB session
            logger.exception("Critical failure during database execution.")
            return ETLExitCode.CRITICAL_FAILURE

        return exit_code

    def _run_with_connection(
        self,
        connection: Any,
        dataset_files: list[Path],
        started_at: datetime,
        start_counter: float,
    ) -> ETLExitCode:
        """Run schema build, loading, validation, and reporting on an open connection.

        Args:
            connection: An open, configured ``sqlite3.Connection`` (Create
                SQLite Connection / Create Database steps already complete
                by the time this is called).
            dataset_files: Every discovered CSV file to load.
            started_at: Timestamp marking the start of the run.
            start_counter: ``time.perf_counter()`` value at the start of the run.

        Returns:
            The :class:`~src.database.etl_pipeline.ETLExitCode` reflecting
            the outcome of the run.
        """
        self._console.print_stage("Creating Database...")
        sql_executor = SQLExecutor(connection)
        database_manager = DatabaseManager(
            connection, schema_builder=self._schema_builder, sql_executor=sql_executor
        )
        data_loader = DataLoader(sql_executor, batch_size=self._database_config.batch_insert_size)
        runner = DatasetLoadRunner(
            database_manager=database_manager,
            data_loader=data_loader,
            connection=connection,
            discovery_root=self._discovery_root,
        )

        dataframes, build_failures = self._load_dataframes(dataset_files)
        schemas = self._build_schemas(dataframes)
        self._schema_builder.infer_foreign_keys(schemas)
        load_order = self._order_resolver.resolve(schemas)

        self._console.print_stage("Creating Tables...")
        # Drop any pre-existing versions of these tables in REVERSE
        # dependency order (children before parents) before creating or
        # loading anything. With PRAGMA foreign_keys = ON, DROP TABLE
        # performs an implicit delete validated against other tables'
        # foreign keys, so a parent cannot be dropped while an old child
        # row still references it.
        for table_name in reversed(load_order):
            try:
                database_manager.drop_table(table_name)
            except Exception:  # noqa: BLE001 - a stale table is not fatal to the run
                logger.exception("Failed to drop pre-existing table '%s'.", table_name)

        records: list[DatasetLoadRecord] = list(build_failures)
        for table_name in load_order:
            self._console.print_dataset_start(table_name)
            record = runner.load(table_name, dataframes[table_name], schemas[table_name])
            records.append(record)
            self._console.print_dataset_result(record)

        self._console.print_stage("Validating Relationships...")
        successfully_loaded_schemas = {
            name: schema
            for name, schema in schemas.items()
            if any(r.dataset_name == name and r.succeeded for r in records)
        }
        database_validator = DatabaseValidator(sql_executor)
        validation_report = database_validator.validate_all(successfully_loaded_schemas)

        total_duration = time.perf_counter() - start_counter

        self._console.print_stage("Generating Reports...")
        try:
            self._report_writer.write_reports(
                database_config=self._database_config,
                schemas=successfully_loaded_schemas,
                records=records,
                database_manager=database_manager,
                validation_report=validation_report,
                started_at=started_at,
                total_duration_seconds=total_duration,
            )
        except Exception:  # noqa: BLE001 - report persistence must not crash the run
            logger.exception("Failed to persist database execution reports.")

        self._console.print_failures(records)
        self._console.print_ready()

        failure_count = sum(1 for r in records if not r.succeeded)
        if failure_count > 0 or not validation_report["overall_valid"]:
            logger.warning(
                "Database executor finished with %d failure(s) and overall_valid=%s.",
                failure_count,
                validation_report["overall_valid"],
            )
            return ETLExitCode.COMPLETED_WITH_FAILURES

        logger.info("Database executor finished successfully: database ready.")
        return ETLExitCode.SUCCESS

    # ------------------------------------------------------------------ #
    # Pipeline stages
    # ------------------------------------------------------------------ #

    def _load_dataframes(
        self, dataset_files: list[Path]
    ) -> tuple[dict[str, Any], list[DatasetLoadRecord]]:
        """Load every discovered CSV into memory as a DataFrame.

        Datasets that fail to load (missing, empty, unparsable) are
        isolated here so schema building and table loading only ever see
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
        """Derive a schema for every successfully loaded dataset (Build Schema step).

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


def main() -> int:
    """Application entry point for the standalone database executor.

    Returns:
        The integer process exit code (see
        :class:`~src.database.etl_pipeline.ETLExitCode`).
    """
    try:
        executor = DatabaseExecutor()
        exit_code = executor.execute()
    except Exception:  # noqa: BLE001 - last-resort safety net at the entry point
        logger.exception("Critical, unhandled failure in the database executor.")
        return int(ETLExitCode.CRITICAL_FAILURE)

    return int(exit_code)


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "DatabaseExecutor",
    "DatasetLoadRunner",
    "DatabaseExecutionReportWriter",
    "DatabaseExecutorConsoleReporter",
    "main",
]
