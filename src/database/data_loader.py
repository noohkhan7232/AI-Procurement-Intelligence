"""Batch row insertion into SQLite tables, with validation and rollback.

Provides :class:`DataLoader`, which inserts every row of a cleaned
DataFrame into its already-materialized table in batches, all within a
single transaction. After the batch inserts complete, the actual row count
in the table is compared against the DataFrame's row count; any mismatch
raises :class:`DataLoadError`, which — because the insert and the count
check both happen inside the same
:class:`~src.database.transaction_manager.TransactionManager` block — is
guaranteed to roll back every row from that dataset's load.

Typical usage:
    >>> from src.database.data_loader import DataLoader
    >>> loader = DataLoader(sql_executor)
    >>> summary = loader.load(dataframe, schema, TransactionManager(connection))
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.config.database_config import DEFAULT_BATCH_INSERT_SIZE
from src.database.schema_builder import TableSchema
from src.database.sql_executor import SQLExecutor
from src.database.transaction_manager import TransactionManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DataLoadError(Exception):
    """Raised when loading a dataset's rows into its table cannot be verified."""


class DataLoader:
    """Inserts a DataFrame's rows into its corresponding SQLite table.

    Single responsibility: row insertion and post-insert count validation.
    Table creation is the responsibility of
    :class:`~src.database.database_manager.DatabaseManager`; transaction
    boundaries are supplied by the caller via an injected
    :class:`~src.database.transaction_manager.TransactionManager`.
    """

    def __init__(
        self, sql_executor: SQLExecutor, *, batch_size: int = DEFAULT_BATCH_INSERT_SIZE
    ) -> None:
        """Initialize the loader.

        Args:
            sql_executor: The statement-execution collaborator used to run
                the batched ``INSERT`` statements.
            batch_size: Number of rows sent per ``executemany`` call.
                Defaults to :data:`src.config.database_config.DEFAULT_BATCH_INSERT_SIZE`.
        """
        self._executor = sql_executor
        self._batch_size = batch_size

    def load(
        self,
        dataframe: pd.DataFrame,
        schema: TableSchema,
        transaction_manager: TransactionManager,
    ) -> dict[str, Any]:
        """Insert every row of a DataFrame into its table within one transaction.

        Args:
            dataframe: The cleaned dataset to load. Its columns must match
                ``schema.column_names()``.
            schema: The already-materialized table's schema.
            transaction_manager: The transaction boundary this load should
                run inside. On any failure (including a post-insert row
                count mismatch), the transaction manager rolls back every
                row inserted for this dataset.

        Returns:
            A summary dictionary with ``table_name``, ``rows_expected``,
            and ``rows_loaded``.

        Raises:
            DataLoadError: If the row count actually present in the table
                after insertion does not match the DataFrame's row count.
        """
        rows_expected = dataframe.shape[0]
        columns = schema.column_names()
        insert_sql = self._build_insert_statement(schema.table_name, columns)
        records = self._dataframe_to_records(dataframe, columns)

        with transaction_manager:
            for batch_start in range(0, len(records), self._batch_size):
                batch = records[batch_start : batch_start + self._batch_size]
                self._executor.execute_many(insert_sql, batch)

            rows_loaded = (
                self._executor.fetch_scalar(
                    f'SELECT COUNT(*) FROM "{schema.table_name}";'
                )
                or 0
            )

            if rows_loaded != rows_expected:
                raise DataLoadError(
                    f"Row count mismatch for table '{schema.table_name}': "
                    f"expected {rows_expected}, found {rows_loaded}. "
                    "Transaction will be rolled back."
                )

        logger.info(
            "Loaded %d row(s) into table '%s' (expected %d).",
            rows_loaded,
            schema.table_name,
            rows_expected,
        )
        return {
            "table_name": schema.table_name,
            "rows_expected": rows_expected,
            "rows_loaded": rows_loaded,
        }

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_insert_statement(table_name: str, columns: list[str]) -> str:
        """Build a parameterized ``INSERT`` statement for a table's columns.

        Args:
            table_name: The target table name.
            columns: The column names to insert, in order.

        Returns:
            A parameterized ``INSERT INTO ... VALUES (?, ?, ...)`` statement.
        """
        column_list = ", ".join(f'"{column}"' for column in columns)
        placeholders = ", ".join("?" for _ in columns)
        return f'INSERT INTO "{table_name}" ({column_list}) VALUES ({placeholders});'

    @staticmethod
    def _dataframe_to_records(
        dataframe: pd.DataFrame, columns: list[str]
    ) -> list[tuple[Any, ...]]:
        """Convert a DataFrame into a list of SQLite-safe parameter tuples.

        Args:
            dataframe: The source dataset.
            columns: The columns to include, in insertion order.

        Returns:
            One tuple of native Python values per row, with missing values
            normalized to ``None`` and pandas-specific types (booleans,
            timestamps) coerced to SQLite-storable equivalents.
        """
        working = dataframe[columns]
        records: list[tuple[Any, ...]] = []
        for row in working.itertuples(index=False, name=None):
            records.append(tuple(DataLoader._coerce_value(value) for value in row))
        return records

    @staticmethod
    def _coerce_value(value: Any) -> Any:
        """Coerce a single cell value into a SQLite-storable native type.

        Args:
            value: The raw cell value from the DataFrame.

        Returns:
            ``None`` for any missing value, ``0``/``1`` for booleans, an
            ISO-8601 string for timestamps, and the value unchanged
            otherwise.
        """
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        try:
            if pd.isna(value):
                return None
        except (TypeError, ValueError):
            pass
        if isinstance(value, pd.Timestamp):
            return value.isoformat()
        return value


__all__ = ["DataLoader", "DataLoadError"]
