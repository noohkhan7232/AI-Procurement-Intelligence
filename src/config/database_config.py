"""Centralized configuration for the SQLite database layer.

Mirrors the pattern established by :mod:`src.config.paths`: a single,
immutable configuration object resolved through one factory function, with
zero hardcoded absolute paths. The database file location is always derived
from :func:`src.config.paths.get_project_paths`, so it automatically
respects the platform's project-root resolution on any machine or OS.

Typical usage:
    >>> from src.config.database_config import get_database_config
    >>> config = get_database_config()
    >>> config.database_path
    PosixPath('/.../database/procurement.db')
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.config.paths import get_project_paths

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Default SQLite database file name.
DEFAULT_DATABASE_FILENAME: str = "procurement.db"

#: Default connection timeout, in seconds, before SQLite raises
#: ``sqlite3.OperationalError: database is locked``.
DEFAULT_CONNECTION_TIMEOUT_SECONDS: float = 30.0

#: Default number of rows inserted per ``executemany`` batch.
DEFAULT_BATCH_INSERT_SIZE: int = 500

#: Default SQLite journal mode. WAL (Write-Ahead Logging) allows concurrent
#: readers during a write and is the recommended production default.
DEFAULT_JOURNAL_MODE: str = "WAL"


@dataclass(frozen=True)
class DatabaseConfig:
    """Immutable configuration for connecting to and operating on the database.

    Attributes:
        database_path: Absolute path to the SQLite database file.
        timeout_seconds: Connection timeout before a "database is locked"
            error is raised.
        enable_foreign_keys: Whether ``PRAGMA foreign_keys = ON`` is applied
            to every connection, enforcing referential integrity at the
            SQLite engine level.
        batch_insert_size: Number of rows inserted per ``executemany`` call
            during data loading.
        journal_mode: The SQLite journal mode applied to every connection.
    """

    database_path: Path
    timeout_seconds: float = DEFAULT_CONNECTION_TIMEOUT_SECONDS
    enable_foreign_keys: bool = True
    batch_insert_size: int = DEFAULT_BATCH_INSERT_SIZE
    journal_mode: str = DEFAULT_JOURNAL_MODE


def get_database_config(
    *, database_filename: str = DEFAULT_DATABASE_FILENAME
) -> DatabaseConfig:
    """Resolve the platform's database configuration.

    Args:
        database_filename: The SQLite database file name, relative to the
            platform's centralized ``database`` directory. Defaults to
            :data:`DEFAULT_DATABASE_FILENAME`.

    Returns:
        A fully resolved, immutable :class:`DatabaseConfig`.
    """
    paths = get_project_paths()
    paths.database_dir.mkdir(parents=True, exist_ok=True)
    database_path = paths.database_dir / database_filename
    return DatabaseConfig(database_path=database_path)


__all__ = ["DatabaseConfig", "get_database_config"]
