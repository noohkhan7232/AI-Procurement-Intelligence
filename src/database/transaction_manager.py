"""Explicit SQLite transaction boundary management.

Provides :class:`TransactionManager`, a context manager giving explicit,
unambiguous control over ``BEGIN``/``COMMIT``/``ROLLBACK`` boundaries. This
requires the connection to have been opened in autocommit mode at the
Python DB-API layer (``isolation_level=None``), which is exactly how
:class:`~src.database.connection.DatabaseConnectionManager` opens every
connection — so ownership of transaction semantics never conflicts between
the two layers.

Typical usage:
    >>> from src.database.transaction_manager import TransactionManager
    >>> with TransactionManager(connection):
    ...     connection.execute("INSERT INTO vendors (...) VALUES (...);")
    # Commits automatically on success; rolls back automatically on any
    # exception raised inside the block, and re-raises it.
"""

from __future__ import annotations

import sqlite3
from types import TracebackType

from src.utils.logger import get_logger

logger = get_logger(__name__)


class TransactionManager:
    """Context manager providing explicit ``BEGIN``/``COMMIT``/``ROLLBACK`` control.

    On successful exit from the ``with`` block, the transaction is
    committed. If any exception propagates out of the block, the
    transaction is rolled back and the exception is re-raised unmodified
    (this manager never swallows errors).
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        """Initialize the transaction manager for a given connection.

        Args:
            connection: An open ``sqlite3.Connection`` in autocommit mode
                (``isolation_level=None``), as produced by
                :class:`~src.database.connection.DatabaseConnectionManager`.
        """
        self._connection = connection
        self._active = False

    def __enter__(self) -> "TransactionManager":
        """Begin a new transaction.

        Returns:
            This :class:`TransactionManager` instance.
        """
        self._connection.execute("BEGIN;")
        self._active = True
        logger.debug("Transaction started.")
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Commit on success, or roll back on exception.

        Args:
            exc_type: The exception type raised inside the block, if any.
            exc_value: The exception instance raised inside the block, if any.
            traceback: The exception traceback, if any.

        Returns:
            ``False`` always, so any exception raised inside the block
            propagates to the caller after rollback.
        """
        if exc_type is None:
            self._connection.execute("COMMIT;")
            logger.debug("Transaction committed.")
        else:
            self._connection.execute("ROLLBACK;")
            logger.warning(
                "Transaction rolled back due to %s: %s", exc_type.__name__, exc_value
            )
        self._active = False
        return False

    @property
    def is_active(self) -> bool:
        """Whether a transaction is currently open on this manager."""
        return self._active


__all__ = ["TransactionManager"]
