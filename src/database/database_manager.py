"""Database-level table and schema management.

Provides :class:`DatabaseManager`, the facade responsible for turning a
derived :class:`~src.database.schema_builder.TableSchema` into real database
objects: executing the generated ``CREATE TABLE``/``CREATE INDEX``
statements, and answering structural questions (which tables exist, how
many rows a table holds) needed by the ETL pipeline and reporting layer.

Typical usage:
    >>> from src.database.database_manager import DatabaseManager
    >>> manager = DatabaseManager(connection)
    >>> manager.create_table(schema)
    >>> manager.get_row_count("orders")
"""

from __future__ import annotations

import sqlite3

from src.database.schema_builder import SchemaBuilder, TableSchema
from src.database.sql_executor import SQLExecutor
from src.database.transaction_manager import TransactionManager
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseManager:
    """Coordinates DDL (table/index creation) for the procurement database.

    Single responsibility: schema materialization and structural
    inspection. Row-level data loading is the responsibility of
    :class:`~src.database.data_loader.DataLoader`.
    """

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        schema_builder: SchemaBuilder | None = None,
        sql_executor: SQLExecutor | None = None,
    ) -> None:
        """Initialize the database manager.

        Args:
            connection: An open, configured ``sqlite3.Connection``.
            schema_builder: The SQL-rendering collaborator used to turn a
                :class:`~src.database.schema_builder.TableSchema` into DDL.
                If ``None``, a default :class:`~src.database.schema_builder.SchemaBuilder`
                is created.
            sql_executor: The statement-execution collaborator. If ``None``,
                a default :class:`~src.database.sql_executor.SQLExecutor`
                wrapping ``connection`` is created.
        """
        self._connection = connection
        self._schema_builder = schema_builder or SchemaBuilder()
        self._executor = sql_executor or SQLExecutor(connection)

    @property
    def sql_executor(self) -> SQLExecutor:
        """The SQL executor this manager operates through."""
        return self._executor

    def create_table(self, schema: TableSchema, *, drop_existing: bool = True) -> None:
        """Materialize a table (and its foreign-key indexes) from a schema.

        Executed as a single transaction: table drop (if requested), table
        creation, and every index creation either all succeed or all roll
        back together.

        Note:
            When recreating multiple related tables in one run, prefer
            dropping every table first via :meth:`drop_table` in *reverse*
            dependency order (children before parents), then call this
            method with ``drop_existing=False``. With
            ``PRAGMA foreign_keys = ON``, ``DROP TABLE`` performs an
            implicit delete that SQLite validates against other tables'
            foreign keys — dropping a parent while an old child row still
            references it fails.

        Args:
            schema: The table schema to materialize.
            drop_existing: Whether to drop any pre-existing table of the
                same name first. Defaults to ``True``; set to ``False``
                when the caller has already dropped it separately (see the
                note above).
        """
        with TransactionManager(self._connection):
            if drop_existing:
                self._executor.execute(f'DROP TABLE IF EXISTS "{schema.table_name}";')

            create_table_sql = self._schema_builder.generate_create_table_sql(schema)
            self._executor.execute(create_table_sql)

            for index_sql in self._schema_builder.generate_index_sql(schema):
                self._executor.execute(index_sql)

        logger.info(
            "Materialized table '%s': %d column(s), primary_key=%s, %d foreign key(s).",
            schema.table_name,
            len(schema.columns),
            schema.primary_key,
            len(schema.foreign_keys),
        )

    def drop_table(self, table_name: str) -> None:
        """Drop a table if it exists, in its own transaction.

        Args:
            table_name: The table to drop.
        """
        with TransactionManager(self._connection):
            self._executor.execute(f'DROP TABLE IF EXISTS "{table_name}";')
        logger.info("Dropped table '%s' (if it existed).", table_name)

    def table_exists(self, table_name: str) -> bool:
        """Check whether a table currently exists in the database.

        Args:
            table_name: The table name to check.

        Returns:
            ``True`` if the table exists, ``False`` otherwise.
        """
        result = self._executor.fetch_scalar(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?;",
            (table_name,),
        )
        return result is not None

    def list_tables(self) -> list[str]:
        """List every user table currently in the database.

        Returns:
            Table names in alphabetical order, excluding SQLite's internal
            ``sqlite_*`` tables.
        """
        rows = self._executor.fetch_all(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name;"
        )
        return [row[0] for row in rows]

    def get_row_count(self, table_name: str) -> int:
        """Count the rows currently stored in a table.

        Args:
            table_name: The table to count rows for.

        Returns:
            The row count.
        """
        return self._executor.fetch_scalar(f'SELECT COUNT(*) FROM "{table_name}";') or 0


__all__ = ["DatabaseManager"]
