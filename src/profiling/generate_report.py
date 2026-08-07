"""Profiling report generation with production-grade run versioning.

Provides :class:`ReportGenerator`, which takes the structured output of
:class:`src.profiling.data_profiler.DataProfiler` and
:class:`src.profiling.quality_checker.DataQualityChecker` and persists it to
disk in multiple formats (CSV, Excel, JSON, and a human-readable plain-text
summary).

Versioning model
-----------------
Every :class:`ReportGenerator` instance represents exactly one execution
("run") of the profiling pipeline. On construction it:

    1. Generates a single run timestamp and creates an immutable folder for
       it under ``reports/profiling/runs/<timestamp>/``.
    2. Clears ``reports/profiling/latest/`` so it no longer reflects a
       previous, unrelated run.

Every report written during the lifetime of that instance is:

    * Saved into that run's dedicated folder (the permanent, historical
      record — never overwritten, never deleted by later runs).
    * Mirrored into ``reports/profiling/latest/`` (a rolling pointer that,
      once a run starts, only ever contains that run's reports).

Because :class:`ReportGenerator` is typically instantiated once per
end-to-end pipeline execution and reused across every dataset processed in
that run (see ``src.main.DatasetProcessor``), ``latest/`` naturally
accumulates the full, newest set of reports for the whole run, while every
prior run remains untouched under ``runs/<timestamp>/``.

Typical usage:
    >>> from src.profiling.generate_report import ReportGenerator
    >>> generator = ReportGenerator()
    >>> paths = generator.generate_full_report(
    ...     dataset_name="vendors",
    ...     profile=profile,
    ...     quality_report=quality_report,
    ... )
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.config.paths import get_project_paths
from src.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Timestamp format used to name each immutable run folder under
#: ``reports/profiling/runs/``. Chosen for lexicographic == chronological
#: sort order, and filesystem safety on Windows/Linux/macOS.
RUN_TIMESTAMP_FORMAT: str = "%Y%m%d_%H%M%S"

#: JSON serialization indentation width.
JSON_INDENT: int = 2

#: Column headers for the flattened column-profile CSV/Excel report.
COLUMN_REPORT_HEADERS: tuple[str, ...] = (
    "column_name",
    "dtype",
    "semantic_type",
    "missing_count",
    "missing_percentage",
    "unique_count",
    "unique_percentage",
)


class ReportGenerator:
    """Persist profiling and quality-check results with run-based versioning.

    Each instance owns exactly one immutable run folder
    (``reports/profiling/runs/<timestamp>/``) and keeps
    ``reports/profiling/latest/`` synchronized with everything written
    during that run. Historical runs are never modified or deleted by later
    ``ReportGenerator`` instances.
    """

    def __init__(self, *, run_timestamp: str | None = None) -> None:
        """Initialize the generator and open a new immutable run folder.

        Args:
            run_timestamp: Explicit run identifier to use instead of the
                current time. Primarily intended for deterministic testing;
                production code should omit this and let the generator
                derive it from the current time. If a run with this
                identifier already exists, its folder is reused (existing
                files within it are left untouched) rather than raising an
                error, so repeated construction with the same explicit
                timestamp is safe.
        """
        self._paths = get_project_paths()
        self._run_timestamp = run_timestamp or datetime.now().strftime(
            RUN_TIMESTAMP_FORMAT
        )
        self._run_dir = self._paths.profiling_runs_dir / self._run_timestamp
        self._latest_dir = self._paths.profiling_latest_dir

        self._run_dir.mkdir(parents=True, exist_ok=True)
        self._reset_latest_directory()

        logger.info(
            "ReportGenerator started new run '%s'. Run directory: %s | "
            "Latest directory: %s",
            self._run_timestamp,
            self._run_dir,
            self._latest_dir,
        )

    # ------------------------------------------------------------------ #
    # Run metadata (read-only)
    # ------------------------------------------------------------------ #

    @property
    def run_timestamp(self) -> str:
        """The unique identifier of the run owned by this instance."""
        return self._run_timestamp

    @property
    def current_run_directory(self) -> Path:
        """The immutable, historical output directory for this run."""
        return self._run_dir

    @property
    def latest_directory(self) -> Path:
        """The rolling directory mirroring this run's newest reports."""
        return self._latest_dir

    def list_previous_runs(self) -> list[Path]:
        """List every historical run directory, oldest first.

        Returns:
            A chronologically sorted list of run directories under
            ``reports/profiling/runs/``, including the current run.
        """
        if not self._paths.profiling_runs_dir.exists():
            return []
        return sorted(
            p for p in self._paths.profiling_runs_dir.iterdir() if p.is_dir()
        )

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def generate_full_report(
        self,
        *,
        dataset_name: str,
        profile: dict[str, Any],
        quality_report: dict[str, Any],
    ) -> dict[str, Path]:
        """Generate every supported report format for a profiled dataset.

        All files are written into this instance's run folder and mirrored
        into ``latest/``.

        Args:
            dataset_name: Human-readable dataset identifier, used to build
                output file names.
            profile: The structural profile produced by
                ``DataProfiler.profile_dataframe``.
            quality_report: The quality report produced by
                ``DataQualityChecker.check_quality``.

        Returns:
            A mapping of report type (``'csv'``, ``'excel'``, ``'json'``,
            ``'summary'``) to the absolute path of the generated file inside
            this run's immutable directory.
        """
        safe_name = self._sanitize_dataset_name(dataset_name)

        logger.info(
            "Generating full profiling report for dataset '%s' (run='%s').",
            dataset_name,
            self._run_timestamp,
        )

        combined: dict[str, Any] = {
            "profile": profile,
            "quality": quality_report,
            "generated_at": datetime.now().isoformat(),
            "run_timestamp": self._run_timestamp,
        }

        output_paths = {
            "csv": self.generate_csv_report(dataset_name=safe_name, profile=profile),
            "excel": self.generate_excel_report(
                dataset_name=safe_name,
                profile=profile,
                quality_report=quality_report,
            ),
            "json": self.generate_json_report(
                dataset_name=safe_name, combined_report=combined
            ),
            "summary": self.generate_summary_report(
                dataset_name=safe_name,
                profile=profile,
                quality_report=quality_report,
            ),
        }

        logger.info(
            "Full profiling report generated for '%s': %s",
            dataset_name,
            {k: str(v) for k, v in output_paths.items()},
        )
        return output_paths

    def generate_csv_report(
        self,
        *,
        dataset_name: str,
        profile: dict[str, Any],
        timestamp: str | None = None,
    ) -> Path:
        """Write the flattened per-column profile as a CSV file.

        Args:
            dataset_name: Sanitized dataset identifier for the filename.
            profile: The structural profile to flatten and export.
            timestamp: Deprecated and ignored. Retained only for backward
                compatibility with callers written against the pre-versioning
                API; the report's version is now determined solely by this
                generator instance's run.

        Returns:
            The absolute path to the written CSV file inside this run's
            immutable directory.
        """
        self._warn_if_legacy_timestamp_passed(timestamp)

        rows = self._flatten_column_profiles(profile)
        output_path = self._run_dir / f"{dataset_name}_profile.csv"

        report_df = pd.DataFrame(rows, columns=list(COLUMN_REPORT_HEADERS))
        report_df.to_csv(output_path, index=False)

        logger.info("CSV report written to '%s'.", output_path)
        self._mirror_to_latest(output_path)
        return output_path

    def generate_excel_report(
        self,
        *,
        dataset_name: str,
        profile: dict[str, Any],
        quality_report: dict[str, Any],
        timestamp: str | None = None,
    ) -> Path:
        """Write a multi-sheet Excel workbook summarizing profile and quality.

        Sheets produced:
            * ``Overview``: dataset-level metrics.
            * ``Columns``: flattened per-column profile.
            * ``Quality Scores``: the four dimension scores + overall score.
            * ``Warnings``: every structured quality warning.

        Args:
            dataset_name: Sanitized dataset identifier for the filename.
            profile: The structural profile to export.
            quality_report: The quality report to export.
            timestamp: Deprecated and ignored. See :meth:`generate_csv_report`.

        Returns:
            The absolute path to the written Excel workbook inside this
            run's immutable directory.
        """
        self._warn_if_legacy_timestamp_passed(timestamp)

        output_path = self._run_dir / f"{dataset_name}_profile.xlsx"

        overview_df = pd.DataFrame(
            list(profile.get("overview", {}).items()), columns=["metric", "value"]
        )
        columns_df = pd.DataFrame(
            self._flatten_column_profiles(profile),
            columns=list(COLUMN_REPORT_HEADERS),
        )
        scores_df = pd.DataFrame(
            list(quality_report.get("scores", {}).items()),
            columns=["metric", "value"],
        )
        warnings_df = pd.DataFrame(quality_report.get("warnings", []))

        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            overview_df.to_excel(writer, sheet_name="Overview", index=False)
            columns_df.to_excel(writer, sheet_name="Columns", index=False)
            scores_df.to_excel(writer, sheet_name="Quality Scores", index=False)
            if not warnings_df.empty:
                warnings_df.to_excel(writer, sheet_name="Warnings", index=False)
            else:
                pd.DataFrame({"message": ["No warnings detected."]}).to_excel(
                    writer, sheet_name="Warnings", index=False
                )

        logger.info("Excel report written to '%s'.", output_path)
        self._mirror_to_latest(output_path)
        return output_path

    def generate_json_report(
        self,
        *,
        dataset_name: str,
        combined_report: dict[str, Any],
        timestamp: str | None = None,
    ) -> Path:
        """Write the full combined profile + quality report as JSON.

        Args:
            dataset_name: Sanitized dataset identifier for the filename.
            combined_report: A dictionary combining profile and quality data,
                typically ``{"profile": ..., "quality": ..., "generated_at": ...}``.
            timestamp: Deprecated and ignored. See :meth:`generate_csv_report`.

        Returns:
            The absolute path to the written JSON file inside this run's
            immutable directory.
        """
        self._warn_if_legacy_timestamp_passed(timestamp)

        output_path = self._run_dir / f"{dataset_name}_profile.json"

        with output_path.open("w", encoding="utf-8") as json_file:
            json.dump(combined_report, json_file, indent=JSON_INDENT, default=str)

        logger.info("JSON report written to '%s'.", output_path)
        self._mirror_to_latest(output_path)
        return output_path

    def generate_summary_report(
        self,
        *,
        dataset_name: str,
        profile: dict[str, Any],
        quality_report: dict[str, Any],
        timestamp: str | None = None,
    ) -> Path:
        """Write a concise, human-readable plain-text summary report.

        Args:
            dataset_name: Sanitized dataset identifier for the filename.
            profile: The structural profile to summarize.
            quality_report: The quality report to summarize.
            timestamp: Deprecated and ignored. See :meth:`generate_csv_report`.

        Returns:
            The absolute path to the written text file inside this run's
            immutable directory.
        """
        self._warn_if_legacy_timestamp_passed(timestamp)

        output_path = self._run_dir / f"{dataset_name}_summary.txt"

        overview = profile.get("overview", {})
        scores = quality_report.get("scores", {})
        warnings = quality_report.get("warnings", [])

        lines = [
            f"Data Profiling Summary — {dataset_name}",
            f"Run: {self._run_timestamp}",
            f"Generated at: {datetime.now().isoformat()}",
            "=" * 60,
            "",
            "OVERVIEW",
            "-" * 60,
            f"Rows: {overview.get('row_count', 'N/A')}",
            f"Columns: {overview.get('column_count', 'N/A')}",
            f"Duplicate rows: {overview.get('duplicate_row_count', 'N/A')} "
            f"({overview.get('duplicate_row_percentage', 'N/A')}%)",
            f"Memory usage: {overview.get('memory_usage_mb', 'N/A')} MB",
            "",
            "QUALITY SCORES",
            "-" * 60,
            f"Completeness: {scores.get('completeness_score', 'N/A')}",
            f"Uniqueness:   {scores.get('uniqueness_score', 'N/A')}",
            f"Consistency:  {scores.get('consistency_score', 'N/A')}",
            f"Validity:     {scores.get('validity_score', 'N/A')}",
            f"Overall:      {scores.get('overall_score', 'N/A')}",
            "",
            f"WARNINGS ({len(warnings)})",
            "-" * 60,
        ]

        if warnings:
            for warning in warnings:
                column = warning.get("column") or "dataset-level"
                lines.append(
                    f"[{warning.get('severity')}] {warning.get('warning_type')} "
                    f"({column}): {warning.get('message')}"
                )
        else:
            lines.append("No warnings detected.")

        output_path.write_text("\n".join(lines), encoding="utf-8")

        logger.info("Summary report written to '%s'.", output_path)
        self._mirror_to_latest(output_path)
        return output_path

    # ------------------------------------------------------------------ #
    # Versioning internals
    # ------------------------------------------------------------------ #

    def _reset_latest_directory(self) -> None:
        """Clear ``latest/`` so it no longer reflects a prior, unrelated run.

        Called exactly once, at construction time. Safe to call even if the
        directory does not yet exist (e.g. on a brand-new project). Never
        touches ``runs/``, so no historical run is ever affected.
        """
        if self._latest_dir.exists():
            shutil.rmtree(self._latest_dir)
        self._latest_dir.mkdir(parents=True, exist_ok=True)
        logger.debug("Cleared '%s' for new run '%s'.", self._latest_dir, self._run_timestamp)

    def _mirror_to_latest(self, source_path: Path) -> Path:
        """Copy a just-written report from the run folder into ``latest/``.

        Copying (rather than moving) keeps the run folder as the permanent,
        untouched record while ``latest/`` acts purely as a same-content
        rolling pointer to the newest reports.

        Args:
            source_path: The file inside ``current_run_directory`` that was
                just written.

        Returns:
            The path to the mirrored copy inside ``latest/``.
        """
        destination_path = self._latest_dir / source_path.name
        shutil.copy2(source_path, destination_path)
        logger.debug("Mirrored '%s' -> '%s'.", source_path, destination_path)
        return destination_path

    @staticmethod
    def _warn_if_legacy_timestamp_passed(timestamp: str | None) -> None:
        """Log a deprecation notice if a caller still passes ``timestamp``.

        Args:
            timestamp: The value passed by the caller, if any.
        """
        if timestamp is not None:
            logger.warning(
                "The 'timestamp' argument is deprecated and ignored; report "
                "versioning is now determined by the ReportGenerator's run "
                "(see 'run_timestamp'). Received timestamp='%s'.",
                timestamp,
            )

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _flatten_column_profiles(profile: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten the nested ``columns`` section of a profile into rows.

        Args:
            profile: The structural profile containing a ``columns`` mapping.

        Returns:
            A list of flat dictionaries, one per column, suitable for
            direct conversion into a ``pandas.DataFrame``.
        """
        rows: list[dict[str, Any]] = []
        for column_name, column_data in profile.get("columns", {}).items():
            rows.append(
                {
                    "column_name": column_name,
                    "dtype": column_data.get("dtype"),
                    "semantic_type": column_data.get("semantic_type"),
                    "missing_count": column_data.get("missing_count"),
                    "missing_percentage": column_data.get("missing_percentage"),
                    "unique_count": column_data.get("unique_count"),
                    "unique_percentage": column_data.get("unique_percentage"),
                }
            )
        return rows

    @staticmethod
    def _sanitize_dataset_name(dataset_name: str) -> str:
        """Sanitize a dataset name for safe use in filenames.

        Args:
            dataset_name: The raw dataset identifier.

        Returns:
            A filesystem-safe version containing only alphanumerics,
            hyphens, and underscores.
        """
        sanitized = "".join(
            char if char.isalnum() or char in ("-", "_") else "_"
            for char in dataset_name.strip()
        )
        return sanitized or "unnamed_dataset"


__all__ = ["ReportGenerator"]