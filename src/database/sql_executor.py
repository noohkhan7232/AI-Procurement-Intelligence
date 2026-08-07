"""Logged, exception-safe SQL statement execution.

Provides :class:`SQLExecutor`, a thin wrapper around a ``sqlite3.Connection``
that centralizes statement logging and converts low-level ``sqlite3.Error``
exceptions into the platform's own :class:`SQLExecutionError`, so every
other database module depends on one consistent execution surface and error
type rather than talking to ``sqlite3`` directly.

Typical usage:
    >>> from src.database.sql_executor import SQLExecutor
    >>> executor = SQLExecutor(connection)
    >>> executor.execute('CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY);')
    >>> count = executor.fetch_scalar('SELECT COUNT(*) FROM t;')
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from typing import Any

from src.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Maximum length of a SQL statement included verbatim in log/error
#: messages, to keep logs readable for large generated statements.
MAX_LOGGED_STATEMENT_LENGTH: int = 500


class SQLExecutionError(Exception):
    """Raised when a SQL statement fails to execute against the database."""


class SQLExecutor:
    """Executes SQL statements against a connection with logging and error wrapping.

    Single responsibility: statement execution only. Connection lifecycle is
    owned by :class:`~src.database.connection.DatabaseConnectionManager`;
    transaction boundaries are owned by
    :class:`~src.database.transaction_manager.TransactionManager`.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Initialize the executor.

        Args:
            connection: An open ``sqlite3.Connection`` to execute against.
        """
        self._connection = connection

    def execute(
        self, statement: str, parameters: Sequence[Any] | dict[str, Any] = ()
    ) -> sqlite3.Cursor:
        """Execute a single SQL statement.

        Args:
            statement: The SQL statement, with ``?`` or named placeholders.
            parameters: Parameter values bound to the statement's placeholders.

        Returns:
            The resulting ``sqlite3.Cursor``.

        Raises:
            SQLExecutionError: If the statement fails to execute.
        """
        try:
            logger.debug(
                "Executing SQL: %s | params=%s",
                self._truncate(statement),
                parameters,
            )
            return self._connection.execute(statement, parameters)
        except sqlite3.Error as exc:
            logger.error(
                "SQL execution failed: %s | statement=%s", exc, self._truncate(statement)
            )
            raise SQLExecutionError(f"Failed to execute statement: {exc}") from exc

    def execute_many(
        self, statement: str, parameter_sets: Sequence[Sequence[Any]]
    ) -> sqlite3.Cursor:
        """Execute a SQL statement once per row of parameters.

        Args:
            statement: The SQL statement, with ``?`` placeholders.
            parameter_sets: One parameter tuple per row to execute.

        Returns:
            The resulting ``sqlite3.Cursor``.

        Raises:
            SQLExecutionError: If the batch execution fails.
        """
        try:
            logger.debug(
                "Executing batch SQL (%d row(s)): %s",
                len(parameter_sets),
                self._truncate(statement),
            )
            return self._connection.executemany(statement, parameter_sets)
        except sqlite3.Error as exc:
            logger.error(
                "Batch SQL execution failed: %s | statement=%s",
                exc,
                self._truncate(statement),
            )
            raise SQLExecutionError(
                f"Failed to execute batch statement: {exc}"
            ) from exc

    def execute_script(self, script: str) -> None:
        """Execute a multi-statement SQL script.

        Args:
            script: One or more semicolon-separated SQL statements.

        Raises:
            SQLExecutionError: If any statement in the script fails.
        """
        try:
            self._connection.executescript(script)
        except sqlite3.Error as exc:
            raise SQLExecutionError(f"Failed to execute script: {exc}") from exc

    def fetch_all(
        self, statement: str, parameters: Sequence[Any] = ()
    ) -> list[sqlite3.Row]:
        """Execute a query and fetch every resulting row.

        Args:
            statement: The SQL query to execute.
            parameters: Parameter values bound to the query's placeholders.

        Returns:
            Every matching row.
        """
        return self.execute(statement, parameters).fetchall()

    def fetch_one(
        self, statement: str, parameters: Sequence[Any] = ()
    ) -> sqlite3.Row | None:
        """Execute a query and fetch the first resulting row.

        Args:
            statement: The SQL query to execute.
            parameters: Parameter values bound to the query's placeholders.

        Returns:
            The first matching row, or ``None`` if there are no results.
        """
        return self.execute(statement, parameters).fetchone()

    def fetch_scalar(self, statement: str, parameters: Sequence[Any] = ()) -> Any:
        """Execute a query and return the first column of its first row.

        Convenience helper for aggregate queries such as
        ``SELECT COUNT(*) FROM ...``.

        Args:
            statement: The SQL query to execute.
            parameters: Parameter values bound to the query's placeholders.

        Returns:
            The scalar value, or ``None`` if there are no results.
        """
        row = self.fetch_one(statement, parameters)
        if row is None:
            return None
        return row[0]

    @staticmethod
    def _truncate(statement: str) -> str:
        """Truncate a statement for inclusion in log/error messages.

        Args:
            statement: The SQL statement to truncate.

        Returns:
            The statement, truncated to :data:`MAX_LOGGED_STATEMENT_LENGTH`
            characters with an ellipsis suffix if it was longer.
        """
        collapsed = " ".join(statement.split())
        if len(collapsed) <= MAX_LOGGED_STATEMENT_LENGTH:
            return collapsed
        return collapsed[:MAX_LOGGED_STATEMENT_LENGTH] + "..."


__all__ = ["SQLExecutor", "SQLExecutionError"]
