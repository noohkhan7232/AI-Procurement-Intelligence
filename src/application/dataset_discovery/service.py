"""Dataset discovery application service.

Exposes DatasetDiscoveryService for recursive dataset search across raw and interim directories.
"""

from __future__ import annotations

from pathlib import Path

CSV_DISCOVERY_PATTERN: str = "*.csv"


class DatasetDiscoveryService:
    """Discovers dataset files on disk. Single responsibility: discovery only."""

    def __init__(self, search_root: Path, *, pattern: str = CSV_DISCOVERY_PATTERN) -> None:
        """Initialize the discovery service.

        Args:
            search_root: Directory to search recursively for datasets.
            pattern: Glob pattern identifying dataset files. Defaults to *.csv.
        """
        self._search_root = search_root
        self._pattern = pattern

    def discover(self) -> list[Path]:
        """Recursively discover matching dataset files under search_root."""
        if not self._search_root.exists() or not self._search_root.is_dir():
            return []

        return sorted(
            [
                file_path.resolve()
                for file_path in self._search_root.rglob(self._pattern)
                if file_path.is_file() and not file_path.name.startswith(".")
            ]
        )


__all__ = ["DatasetDiscoveryService", "CSV_DISCOVERY_PATTERN"]
