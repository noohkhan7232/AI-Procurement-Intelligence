"""Centralized path configuration for the AI Procurement Intelligence Platform.

This module is the single source of truth for every filesystem location used
across the platform. It resolves the project root dynamically (no hardcoded
absolute paths) and exposes strongly-typed, cross-platform ``Path`` objects
for every logical data/reporting/logging area of the system.

Design goals:
    * Zero hardcoded absolute paths (Windows/Linux/macOS compatible).
    * Single Responsibility: this module only resolves and exposes paths.
    * Idempotent directory creation via :meth:`ProjectPaths.ensure_directories`.
    * Immutable configuration object (frozen dataclass) to prevent accidental
      mutation of path configuration at runtime.

Typical usage:
    >>> from src.config.paths import get_project_paths
    >>> paths = get_project_paths()
    >>> paths.raw_data_dir
    PosixPath('/.../data/raw')
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Marker files/directories used to detect the project root when walking up
#: the directory tree from this file's location. The search stops at the
#: first ancestor directory that contains ANY of these markers.
_PROJECT_ROOT_MARKERS: tuple[str, ...] = (
    ".git",
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
)

#: Maximum number of parent directories to traverse while searching for the
#: project root. Prevents infinite loops on unusual filesystem layouts.
_MAX_ROOT_SEARCH_DEPTH: int = 10


# --------------------------------------------------------------------------- #
# Root resolution
# --------------------------------------------------------------------------- #


def _find_project_root(start_path: Path) -> Path:
    """Locate the project root by walking up from ``start_path``.

    The project root is identified as the first ancestor directory that
    contains one of the well-known marker files/directories defined in
    :data:`_PROJECT_ROOT_MARKERS`. If no marker is found within
    :data:`_MAX_ROOT_SEARCH_DEPTH` levels, the search falls back to the
    directory that contains the top-level ``src`` package.

    Args:
        start_path: The path to begin searching from, typically the
            location of this module on disk.

    Returns:
        The resolved absolute path to the project root.
    """
    current = start_path.resolve()

    for _ in range(_MAX_ROOT_SEARCH_DEPTH):
        if any((current / marker).exists() for marker in _PROJECT_ROOT_MARKERS):
            return current
        if current.parent == current:
            break
        current = current.parent

    # Fallback: assume project root is two levels above this file
    # (src/config/paths.py -> src -> project_root).
    fallback_root = start_path.resolve().parents[1]
    return fallback_root


# --------------------------------------------------------------------------- #
# Path configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ProjectPaths:
    """Immutable container exposing every filesystem location used by the
    platform, all rooted at a single dynamically-resolved project root.

    Attributes:
        root: Absolute path to the project root directory.
        data_dir: Root directory for all data artifacts.
        raw_data_dir: Directory containing untouched, source-of-truth data.
        processed_data_dir: Directory containing fully cleaned/transformed data.
        interim_data_dir: Directory containing intermediate transformation data.
        synthetic_data_dir: Directory containing generated/synthetic datasets.
        reports_dir: Root directory for all generated reports.
        profiling_reports_dir: Directory for data profiling reports.
        profiling_latest_dir: Directory always containing only the most
            recently generated set of profiling reports.
        profiling_runs_dir: Directory containing one immutable,
            timestamped subfolder per historical profiling execution.
        cleaning_reports_dir: Directory for data cleaning reports.
        cleaning_latest_dir: Directory always containing only the most
            recently generated set of cleaning reports.
        cleaning_runs_dir: Directory containing one immutable, timestamped
            subfolder per historical cleaning execution.
        database_reports_dir: Directory for database ETL and executor reports.
        database_latest_dir: Directory always containing only the most
            recently generated set of database reports.
        database_runs_dir: Directory containing one immutable, timestamped
            subfolder per historical database execution run.
        simulation_reports_dir: Directory for transaction simulation reports.
        simulation_latest_dir: Directory always containing only the most
            recently generated set of simulation reports.
        simulation_runs_dir: Directory containing one immutable, timestamped
            subfolder per historical simulation execution run.
        simulation_data_dir: Directory containing generated transaction datasets.
        logs_dir: Directory containing rotating application log files.
        database_dir: Directory containing local database files (e.g. SQLite).
        sql_dir: Directory containing SQL scripts/migrations.
    """

    root: Path
    data_dir: Path = field(init=False)
    raw_data_dir: Path = field(init=False)
    processed_data_dir: Path = field(init=False)
    interim_data_dir: Path = field(init=False)
    synthetic_data_dir: Path = field(init=False)
    simulation_data_dir: Path = field(init=False)
    reports_dir: Path = field(init=False)
    profiling_reports_dir: Path = field(init=False)
    profiling_latest_dir: Path = field(init=False)
    profiling_runs_dir: Path = field(init=False)
    cleaning_reports_dir: Path = field(init=False)
    cleaning_latest_dir: Path = field(init=False)
    cleaning_runs_dir: Path = field(init=False)
    database_reports_dir: Path = field(init=False)
    database_latest_dir: Path = field(init=False)
    database_runs_dir: Path = field(init=False)
    simulation_reports_dir: Path = field(init=False)
    simulation_latest_dir: Path = field(init=False)
    simulation_runs_dir: Path = field(init=False)
    logs_dir: Path = field(init=False)
    database_dir: Path = field(init=False)
    sql_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        """Derive all dependent paths from ``root`` after initialization.

        Uses ``object.__setattr__`` because the dataclass is frozen; this is
        the standard pattern for computing derived fields on frozen
        dataclasses.
        """
        object.__setattr__(self, "data_dir", self.root / "data")
        object.__setattr__(self, "raw_data_dir", self.data_dir / "raw")
        object.__setattr__(self, "processed_data_dir", self.data_dir / "processed")
        object.__setattr__(self, "interim_data_dir", self.data_dir / "interim")
        object.__setattr__(self, "synthetic_data_dir", self.data_dir / "synthetic")
        object.__setattr__(self, "simulation_data_dir", self.synthetic_data_dir / "simulation")
        object.__setattr__(self, "reports_dir", self.root / "reports")
        object.__setattr__(
            self, "profiling_reports_dir", self.reports_dir / "profiling"
        )
        object.__setattr__(
            self, "profiling_latest_dir", self.profiling_reports_dir / "latest"
        )
        object.__setattr__(
            self, "profiling_runs_dir", self.profiling_reports_dir / "runs"
        )
        object.__setattr__(
            self, "cleaning_reports_dir", self.reports_dir / "cleaning"
        )
        object.__setattr__(
            self, "cleaning_latest_dir", self.cleaning_reports_dir / "latest"
        )
        object.__setattr__(
            self, "cleaning_runs_dir", self.cleaning_reports_dir / "runs"
        )
        object.__setattr__(
            self, "database_reports_dir", self.reports_dir / "database"
        )
        object.__setattr__(
            self, "database_latest_dir", self.database_reports_dir / "latest"
        )
        object.__setattr__(
            self, "database_runs_dir", self.database_reports_dir / "runs"
        )
        object.__setattr__(
            self, "simulation_reports_dir", self.reports_dir / "simulation"
        )
        object.__setattr__(
            self, "simulation_latest_dir", self.simulation_reports_dir / "latest"
        )
        object.__setattr__(
            self, "simulation_runs_dir", self.simulation_reports_dir / "runs"
        )
        object.__setattr__(self, "logs_dir", self.root / "logs")
        object.__setattr__(self, "database_dir", self.root / "database")
        object.__setattr__(self, "sql_dir", self.root / "sql")

    def all_directories(self) -> tuple[Path, ...]:
        """Return every managed directory as a tuple.

        Returns:
            A tuple of all logical directories tracked by this configuration
            object (excluding ``root`` itself, which is expected to already
            exist).
        """
        return (
            self.data_dir,
            self.raw_data_dir,
            self.processed_data_dir,
            self.interim_data_dir,
            self.synthetic_data_dir,
            self.simulation_data_dir,
            self.reports_dir,
            self.profiling_reports_dir,
            self.profiling_latest_dir,
            self.profiling_runs_dir,
            self.cleaning_reports_dir,
            self.cleaning_latest_dir,
            self.cleaning_runs_dir,
            self.database_reports_dir,
            self.database_latest_dir,
            self.database_runs_dir,
            self.simulation_reports_dir,
            self.simulation_latest_dir,
            self.simulation_runs_dir,
            self.logs_dir,
            self.database_dir,
            self.sql_dir,
        )

    def ensure_directories(self) -> None:
        """Create every managed directory on disk if it does not exist.

        This method is idempotent and safe to call multiple times, e.g. at
        application startup.
        """
        for directory in self.all_directories():
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_project_paths() -> ProjectPaths:
    """Return the singleton :class:`ProjectPaths` instance for this process.

    The result is memoized via :func:`functools.lru_cache` so that project
    root resolution (a filesystem walk) only happens once per process.

    Returns:
        The resolved, immutable :class:`ProjectPaths` configuration object.
    """
    root = _find_project_root(Path(__file__).parent)
    paths = ProjectPaths(root=root)
    paths.ensure_directories()
    return paths


__all__ = ["ProjectPaths", "get_project_paths"]