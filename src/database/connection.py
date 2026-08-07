"""SQLite connection creation and lifecycle management.

Provides :class:`DatabaseConnectionManager`, the single place in the
platform that knows how to open a correctly configured
``sqlite3.Connection``: foreign key enforcement, journal mode, row
factory, and manual (autocommit) transaction control so that
:class:`~src.database.transaction_manager.TransactionManager` has
exclusive, unambiguous ownership of transaction boundaries.

Typical usage:
    >>> from src.config.database_config import get_database_config
    >>> from src.database.connection import DatabaseConnectionManager
    >>> manager = DatabaseConnectionManager(get_database_config())
    >>> with manager.connect() as connection:
    ...     connection.execute("SELECT 1;")
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

from src.config.database_config import DatabaseConfig
from src.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Isolation level passed to ``sqlite3.connect``. ``None`` puts the
#: connection in autocommit mode at the Python DB-API layer, so every
#: transaction boundary is instead controlled explicitly and exclusively by
#: :class:`~src.database.transaction_manager.TransactionManager` via
#: ``BEGIN``/``COMMIT``/``ROLLBACK`` statements.
AUTOCOMMIT_ISOLATION_LEVEL: str | None = None


class DatabaseConnectionManager:
    """Creates SQLite connections configured per the platform's :class:`DatabaseConfig`.

    Single responsibility: connection creation and pragma configuration.
    Transaction control lives in
    :class:`~src.database.transaction_manager.TransactionManager`; SQL
    execution lives in :class:`~src.database.sql_executor.SQLExecutor`.
    """

    def __init__(self, config: DatabaseConfig) -> None:
        """Initialize the connection manager.

        Args:
            config: The resolved database configuration to apply to every
                connection this manager creates.
        """
        self._config = config

    def create_connection(self) -> sqlite3.Connection:
        """Open and configure a new SQLite connection.

        Returns:
            A ``sqlite3.Connection`` with ``PRAGMA foreign_keys`` and
            ``PRAGMA journal_mode`` applied per the active configuration,
            ``row_factory`` set to ``sqlite3.Row`` for named column access,
            and autocommit enabled at the DB-API layer for explicit
            transaction control.
        """
        self._config.database_path.parent.mkdir(parents=True, exist_ok=True)

        connection = sqlite3.connect(
            str(self._config.database_path),
            timeout=self._config.timeout_seconds,
            isolation_level=AUTOCOMMIT_ISOLATION_LEVEL,
        )
        connection.row_factory = sqlite3.Row
        self._apply_pragmas(connection)

        logger.info(
            "Opened SQLite connection to '%s' (foreign_keys=%s, journal_mode=%s).",
            self._config.database_path,
            self._config.enable_foreign_keys,
            self._config.journal_mode,
        )
        return connection

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection as a context manager, closing it on exit.

        Yields:
            A configured ``sqlite3.Connection``.
        """
        connection = self.create_connection()
        try:
            yield connection
        finally:
            connection.close()
            logger.debug(
                "Closed SQLite connection to '%s'.", self._config.database_path
            )

    def _apply_pragmas(self, connection: sqlite3.Connection) -> None:
        """Apply the configured PRAGMA statements to a fresh connection.

        Args:
            connection: The connection to configure.
        """
        if self._config.enable_foreign_keys:
            connection.execute("PRAGMA foreign_keys = ON;")
        connection.execute(f"PRAGMA journal_mode = {self._config.journal_mode};")


__all__ = ["DatabaseConnectionManager"]
