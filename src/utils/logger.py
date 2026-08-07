"""Enterprise-grade, reusable logging utility.

Provides a single :func:`get_logger` factory that returns fully configured
``logging.Logger`` instances with:

    * Console (stream) logging.
    * Rotating file logging (size-based rotation) so log files never grow
      unbounded.
    * A consistent, information-rich log format (timestamp, level, module,
      line number, message).
    * Automatic creation of the log directory.
    * Idempotent handler attachment — calling :func:`get_logger` repeatedly
      for the same module name never duplicates handlers.

Typical usage:
    >>> from src.utils.logger import get_logger
    >>> logger = get_logger(__name__)
    >>> logger.info("Profiling started")
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from src.config.paths import get_project_paths

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Default log level applied to newly created loggers.
DEFAULT_LOG_LEVEL: int = logging.INFO

#: Log message format shared by both console and file handlers.
LOG_FORMAT: str = (
    "%(asctime)s | %(levelname)-8s | %(name)s | "
    "%(filename)s:%(lineno)d | %(message)s"
)

#: Timestamp format used within log messages.
DATE_FORMAT: str = "%Y-%m-%d %H:%M:%S"

#: Default rotating log file name shared by all loggers in the platform.
DEFAULT_LOG_FILE_NAME: str = "ai_procurement_intelligence.log"

#: Maximum size (in bytes) a single log file may reach before rotating.
#: 10 MB per file.
MAX_LOG_FILE_BYTES: int = 10 * 1024 * 1024

#: Number of rotated backup log files to retain.
BACKUP_COUNT: int = 5


def _build_formatter() -> logging.Formatter:
    """Build the shared :class:`logging.Formatter` used by all handlers.

    Returns:
        A configured ``logging.Formatter`` instance.
    """
    return logging.Formatter(fmt=LOG_FORMAT, datefmt=DATE_FORMAT)


def _build_console_handler(formatter: logging.Formatter) -> logging.StreamHandler:
    """Build a console (stdout) logging handler.

    Args:
        formatter: The formatter to attach to the handler.

    Returns:
        A configured ``logging.StreamHandler`` instance.
    """
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    handler.setLevel(DEFAULT_LOG_LEVEL)
    return handler


def _build_file_handler(
    log_file_path: Path, formatter: logging.Formatter
) -> RotatingFileHandler:
    """Build a rotating file logging handler.

    Args:
        log_file_path: Absolute path to the log file.
        formatter: The formatter to attach to the handler.

    Returns:
        A configured ``RotatingFileHandler`` instance.
    """
    handler = RotatingFileHandler(
        filename=log_file_path,
        maxBytes=MAX_LOG_FILE_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)
    handler.setLevel(DEFAULT_LOG_LEVEL)
    return handler


def get_logger(
    name: str,
    *,
    log_level: int = DEFAULT_LOG_LEVEL,
    log_file_name: str = DEFAULT_LOG_FILE_NAME,
) -> logging.Logger:
    """Return a fully configured logger for the given module name.

    The returned logger writes to both the console and a rotating log file
    located inside the platform's centralized ``logs`` directory (resolved
    via :func:`src.config.paths.get_project_paths`). Repeated calls with the
    same ``name`` return the same logger instance without duplicating
    handlers.

    Args:
        name: Logical name of the logger, conventionally ``__name__`` of the
            calling module.
        log_level: The minimum severity level this logger will emit.
            Defaults to :data:`DEFAULT_LOG_LEVEL`.
        log_file_name: Name of the rotating log file to write to, relative
            to the platform's ``logs`` directory. Defaults to
            :data:`DEFAULT_LOG_FILE_NAME`.

    Returns:
        A configured ``logging.Logger`` ready for immediate use.
    """
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Prevent duplicate handlers if get_logger() is called multiple times
    # for the same logger name (e.g. across repeated imports/tests).
    if logger.handlers:
        return logger

    logger.propagate = False

    paths = get_project_paths()
    paths.logs_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = paths.logs_dir / log_file_name

    formatter = _build_formatter()
    logger.addHandler(_build_console_handler(formatter))
    logger.addHandler(_build_file_handler(log_file_path, formatter))

    return logger


__all__ = ["get_logger", "DEFAULT_LOG_LEVEL", "DEFAULT_LOG_FILE_NAME"]