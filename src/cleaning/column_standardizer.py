"""Column naming standardization.

Provides :class:`ColumnStandardizer`, which normalizes every column name in
a ``pandas.DataFrame`` to a consistent, analytics-friendly ``snake_case``
convention: trimmed of surrounding whitespace, stripped of special
characters, collapsed to single underscores, and guaranteed unique even if
normalization would otherwise produce collisions.

Scope note: this module standardizes column *headers* only. Standardizing
the *values* within columns (trimming string cell contents, coercing types)
is the responsibility of :class:`~src.cleaning.datatype_standardizer.DataTypeStandardizer`
and, where business-specific, :class:`~src.cleaning.business_rule_validator.BusinessRuleValidator`,
keeping each module single-purpose.

Typical usage:
    >>> from src.cleaning.column_standardizer import ColumnStandardizer
    >>> from src.utils.cleaning_logger import CleaningLogger
    >>> standardizer = ColumnStandardizer(CleaningLogger(__name__))
    >>> cleaned_df, report = standardizer.standardize(df, dataset_name="vendors")
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd

from src.utils.cleaning_logger import CleaningLogger

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Module identifier used in structured log entries.
MODULE_NAME: str = "ColumnStandardizer"

#: Regex matching any run of one-or-more characters that are not lowercase
#: ASCII letters, digits, or underscores. Every such run is collapsed into a
#: single underscore.
_NON_ALPHANUMERIC_RUN_PATTERN = re.compile(r"[^a-z0-9]+")

#: Regex matching two-or-more consecutive underscores, collapsed to one.
_MULTIPLE_UNDERSCORE_PATTERN = re.compile(r"_{2,}")

#: Prefix applied when a normalized name would otherwise start with a digit
#: (making it an invalid Python identifier / awkward SQL column name).
_LEADING_DIGIT_PREFIX: str = "col_"

#: Fallback base name used when normalization produces an empty string.
_EMPTY_NAME_FALLBACK: str = "column"


class ColumnStandardizer:
    """Normalize DataFrame column names to a consistent snake_case convention.

    Single responsibility: column *naming* only. This class never inspects
    or modifies cell values.
    """

    def __init__(self, cleaning_logger: CleaningLogger) -> None:
        """Initialize the standardizer.

        Args:
            cleaning_logger: Shared structured logger used to record every
                rename decision for later inclusion in reports.
        """
        self._logger = cleaning_logger

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def standardize(
        self, dataframe: pd.DataFrame, *, dataset_name: str
    ) -> tuple[pd.DataFrame, dict[str, Any]]:
        """Normalize every column name in the DataFrame.

        Args:
            dataframe: The dataset whose columns will be renamed. Not
                modified in place; a renamed copy is returned.
            dataset_name: Human-readable dataset identifier, used for
                logging and embedded in the returned report.

        Returns:
            A tuple of ``(renamed_dataframe, report)`` where ``report`` is a
            JSON-serializable dictionary mapping original to new column
            names, plus any duplicate-name conflicts that had to be
            resolved.
        """
        working_df = dataframe.copy()
        original_names = [str(col) for col in working_df.columns]

        normalized_names: list[str] = []
        seen_names: dict[str, int] = {}
        duplicate_conflicts: list[dict[str, str]] = []

        for original_name in original_names:
            normalized = self._normalize_name(original_name, index=len(normalized_names))

            if normalized in seen_names:
                seen_names[normalized] += 1
                deduplicated = f"{normalized}_{seen_names[normalized]}"
                duplicate_conflicts.append(
                    {
                        "original_name": original_name,
                        "normalized_name": normalized,
                        "resolved_name": deduplicated,
                    }
                )
                normalized = deduplicated
            else:
                seen_names[normalized] = 0

            normalized_names.append(normalized)

        rename_map = dict(zip(original_names, normalized_names))
        working_df.columns = normalized_names

        changed_map = {
            original: new for original, new in rename_map.items() if original != new
        }

        report = {
            "dataset_name": dataset_name,
            "columns_renamed": len(changed_map),
            "column_renames": changed_map,
            "duplicate_name_conflicts_resolved": duplicate_conflicts,
        }

        self._logger.log_operation(
            dataset_name=dataset_name,
            module=MODULE_NAME,
            operation="standardize_column_names",
            message=(
                f"Renamed {len(changed_map)} of {len(original_names)} "
                f"column(s) to snake_case; resolved "
                f"{len(duplicate_conflicts)} naming conflict(s)."
            ),
            details=report,
        )

        return working_df, report

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_name(name: str, *, index: int) -> str:
        """Normalize a single column name to snake_case.

        Steps: trim whitespace, lowercase, replace any run of non
        alphanumeric characters with a single underscore, collapse repeated
        underscores, strip leading/trailing underscores, and guarantee a
        non-empty, non-digit-leading result.

        Args:
            name: The original column name.
            index: The positional index of this column, used to build a
                deterministic fallback name if normalization would
                otherwise yield an empty string.

        Returns:
            The normalized column name.
        """
        trimmed = name.strip()
        lowered = trimmed.lower()
        underscored = _NON_ALPHANUMERIC_RUN_PATTERN.sub("_", lowered)
        collapsed = _MULTIPLE_UNDERSCORE_PATTERN.sub("_", underscored)
        stripped = collapsed.strip("_")

        if not stripped:
            stripped = f"{_EMPTY_NAME_FALLBACK}_{index}"
        elif stripped[0].isdigit():
            stripped = f"{_LEADING_DIGIT_PREFIX}{stripped}"

        return stripped


__all__ = ["ColumnStandardizer"]
