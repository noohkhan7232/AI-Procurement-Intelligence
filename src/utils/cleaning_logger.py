"""Structured, domain-specific logging for the data cleaning framework.

Every cleaning module (missing value handling, duplicate removal, type
standardization, column standardization, business rule validation) performs
many small transformations that are individually worth recording — not just
as free-text log lines, but as structured entries that can later be
flattened directly into a Transformation Report.

:class:`CleaningLogger` wraps the platform's standard
:func:`src.utils.logger.get_logger` (so console/file logging behavior stays
consistent everywhere) while additionally accumulating an in-memory,
queryable audit trail for the lifetime of a cleaning run.

Typical usage:
    >>> from src.utils.cleaning_logger import CleaningLogger
    >>> clog = CleaningLogger(__name__)
    >>> clog.log_operation(
    ...     dataset_name="vendors",
    ...     module="MissingValueHandler",
    ...     operation="fill_mean",
    ...     message="Filled 12 missing values in 'price' using column mean.",
    ...     details={"column": "price", "filled_count": 12},
    ... )
    >>> clog.entries_as_dicts(dataset_name="vendors")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from src.utils.logger import get_logger

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Log levels accepted by :meth:`CleaningLogger.log_operation`, mapped to
#: the underlying ``logging.Logger`` method name.
_VALID_LOG_LEVELS: tuple[str, ...] = ("debug", "info", "warning", "error", "critical")

#: Default log level applied when the caller does not specify one.
DEFAULT_OPERATION_LOG_LEVEL: str = "info"


@dataclass
class CleaningLogEntry:
    """A single structured record of one cleaning operation.

    Attributes:
        timestamp: ISO-8601 timestamp of when the operation was recorded.
        dataset_name: The dataset the operation was performed on.
        module: The name of the cleaning module that performed the
            operation (e.g. ``'MissingValueHandler'``).
        operation: A short, stable identifier for the kind of operation
            performed (e.g. ``'fill_mean'``, ``'drop_duplicates'``).
        message: A human-readable description of what happened.
        details: Arbitrary structured metadata about the operation (e.g.
            affected column, counts, before/after values).
    """

    timestamp: str
    dataset_name: str
    module: str
    operation: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


class CleaningLogger:
    """Structured logger and in-memory audit trail for cleaning operations.

    In addition to emitting standard log lines through the platform's
    shared logger, every call to :meth:`log_operation` is retained as a
    :class:`CleaningLogEntry`, letting downstream reporting code (e.g. the
    cleaning pipeline's Transformation Report) query exactly what happened,
    per dataset, without re-parsing log files.
    """

    def __init__(self, name: str) -> None:
        """Initialize the structured logger.

        Args:
            name: Logical name for the underlying logger, conventionally
                ``__name__`` of the calling module.
        """
        self._logger = get_logger(name)
        self._entries: list[CleaningLogEntry] = []

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #

    def log_operation(
        self,
        *,
        dataset_name: str,
        module: str,
        operation: str,
        message: str,
        details: dict[str, Any] | None = None,
        level: str = DEFAULT_OPERATION_LOG_LEVEL,
    ) -> CleaningLogEntry:
        """Record a structured cleaning operation and emit a log line.

        Args:
            dataset_name: The dataset the operation was performed on.
            module: The name of the cleaning module performing the
                operation.
            operation: A short, stable identifier for the operation kind.
            message: A human-readable description of the operation.
            details: Arbitrary structured metadata about the operation.
            level: The severity to log at. One of ``'debug'``, ``'info'``,
                ``'warning'``, ``'error'``, ``'critical'``. Defaults to
                :data:`DEFAULT_OPERATION_LOG_LEVEL`.

        Returns:
            The recorded :class:`CleaningLogEntry`.
        """
        normalized_level = level.lower() if level.lower() in _VALID_LOG_LEVELS else (
            DEFAULT_OPERATION_LOG_LEVEL
        )

        entry = CleaningLogEntry(
            timestamp=datetime.now().isoformat(),
            dataset_name=dataset_name,
            module=module,
            operation=operation,
            message=message,
            details=details or {},
        )
        self._entries.append(entry)

        log_method = getattr(self._logger, normalized_level)
        log_method("[%s] %s.%s: %s", dataset_name, module, operation, message)

        return entry

    # ------------------------------------------------------------------ #
    # Querying
    # ------------------------------------------------------------------ #

    def get_entries(self, dataset_name: str | None = None) -> list[CleaningLogEntry]:
        """Retrieve recorded entries, optionally filtered by dataset.

        Args:
            dataset_name: If provided, only entries for this dataset are
                returned. If ``None``, every recorded entry is returned.

        Returns:
            A list of matching :class:`CleaningLogEntry` objects, in the
            order they were recorded.
        """
        if dataset_name is None:
            return list(self._entries)
        return [entry for entry in self._entries if entry.dataset_name == dataset_name]

    def entries_as_dicts(
        self, dataset_name: str | None = None
    ) -> list[dict[str, Any]]:
        """Retrieve recorded entries as JSON-serializable dictionaries.

        Args:
            dataset_name: If provided, only entries for this dataset are
                returned.

        Returns:
            A list of plain dictionaries, one per matching entry.
        """
        return [
            {
                "timestamp": e.timestamp,
                "dataset_name": e.dataset_name,
                "module": e.module,
                "operation": e.operation,
                "message": e.message,
                "details": e.details,
            }
            for e in self.get_entries(dataset_name)
        ]

    def clear(self, dataset_name: str | None = None) -> None:
        """Clear recorded entries.

        Args:
            dataset_name: If provided, only entries for this dataset are
                removed. If ``None``, every recorded entry is cleared.
        """
        if dataset_name is None:
            self._entries.clear()
        else:
            self._entries = [
                entry for entry in self._entries if entry.dataset_name != dataset_name
            ]


__all__ = ["CleaningLogger", "CleaningLogEntry"]
