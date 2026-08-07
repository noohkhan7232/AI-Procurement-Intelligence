"""Declarative business rule validation.

Provides :class:`BusinessRuleValidator`, a rule-engine style validator built
around a single abstraction — :class:`ValidationRule`, a named predicate
over a DataFrame — so that business rules can be registered, composed, and
reported on uniformly regardless of what they check.

No dataset or column name is ever hardcoded in this module. Convenience
factory methods (``add_non_negative_rule``, ``add_date_order_rule``,
``add_primary_key_not_null_rule``, ``add_unique_id_rule``) let callers wire
up rules for the columns *their* dataset actually has, and
:meth:`BusinessRuleValidator.build_default_ruleset` derives a reasonable
starting ruleset purely from column-name heuristics (price/cost/amount ->
non-negative, id-like columns -> not-null + unique, order/delivery date
pairs -> chronological order) that works for any dataset, not just one
particular source system.

Typical usage:
    >>> from src.cleaning.business_rule_validator import BusinessRuleValidator
    >>> from src.utils.cleaning_logger import CleaningLogger
    >>> validator = BusinessRuleValidator(CleaningLogger(__name__))
    >>> validator.add_non_negative_rule("price")
    >>> validator.add_primary_key_not_null_rule("order_id")
    >>> report = validator.validate(df, dataset_name="orders")
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

from src.utils.cleaning_logger import CleaningLogger

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Module identifier used in structured log entries.
MODULE_NAME: str = "BusinessRuleValidator"

#: Maximum number of violating row indices retained per rule in the report,
#: to keep report payloads bounded on large datasets.
MAX_SAMPLE_VIOLATIONS: int = 10

#: Column-name substrings (case-insensitive) that heuristically indicate a
#: non-negative numeric quantity, used by :meth:`build_default_ruleset`.
NON_NEGATIVE_NAME_HINTS: tuple[str, ...] = (
    "price",
    "cost",
    "amount",
    "quantity",
    "qty",
    "value",
    "total",
    "spend",
    "fee",
    "freight",
)

#: Column-name substrings (case-insensitive) that heuristically indicate an
#: identifier column, used by :meth:`build_default_ruleset`.
ID_NAME_HINTS: tuple[str, ...] = ("_id", "id_", "key")

#: Column-name substrings (case-insensitive) identifying a "start"-style
#: date column (e.g. an order date), used by :meth:`build_default_ruleset`.
START_DATE_NAME_HINTS: tuple[str, ...] = ("order_date", "purchase_date", "start_date")

#: Column-name substrings (case-insensitive) identifying an "end"-style date
#: column (e.g. a delivery date), used by :meth:`build_default_ruleset`.
END_DATE_NAME_HINTS: tuple[str, ...] = (
    "delivery_date",
    "ship_date",
    "shipped_date",
    "end_date",
)


class RuleSeverity(str, Enum):
    """Severity levels for business rule violations."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class ValidationRule:
    """A single named, reusable business rule.

    Attributes:
        name: A short, unique identifier for the rule.
        description: A human-readable description of what the rule checks.
        validator: A callable that accepts the DataFrame and returns a
            boolean ``Series`` (same index as the DataFrame) where ``True``
            marks a row that *violates* the rule.
        severity: How serious a violation of this rule is.
    """

    name: str
    description: str
    validator: Callable[[pd.DataFrame], pd.Series]
    severity: RuleSeverity = RuleSeverity.HIGH


class BusinessRuleValidator:
    """Register and evaluate business rules against a dataset.

    Rules are plain predicates over a DataFrame, so any business constraint
    expressible as "which rows violate this?" can be registered — the
    built-in factory methods cover the common cases (non-negativity, date
    ordering, primary-key integrity, uniqueness) but
    :meth:`add_custom_rule` accepts arbitrary logic for anything else.
    """

    def __init__(self, cleaning_logger: CleaningLogger) -> None:
        """Initialize the validator with an empty ruleset.

        Args:
            cleaning_logger: Shared structured logger used to record rule
                registration and validation outcomes.
        """
        self._logger = cleaning_logger
        self._rules: list[ValidationRule] = []

    @property
    def registered_rule_count(self) -> int:
        """The number of rules currently registered on this validator."""
        return len(self._rules)

    def has_rules(self) -> bool:
        """Whether at least one rule has been registered.

        Returns:
            ``True`` if one or more rules are registered, ``False``
            otherwise.
        """
        return bool(self._rules)

    # ------------------------------------------------------------------ #
    # Rule registration
    # ------------------------------------------------------------------ #

    def add_custom_rule(
        self,
        *,
        name: str,
        description: str,
        validator: Callable[[pd.DataFrame], pd.Series],
        severity: RuleSeverity = RuleSeverity.HIGH,
    ) -> None:
        """Register an arbitrary custom business rule.

        Args:
            name: A short, unique identifier for the rule.
            description: A human-readable description of the rule.
            validator: A callable ``(DataFrame) -> Series[bool]`` where
                ``True`` marks a violating row.
            severity: The severity of a violation of this rule.
        """
        self._rules.append(
            ValidationRule(
                name=name, description=description, validator=validator, severity=severity
            )
        )

    def add_non_negative_rule(
        self, column_name: str, *, severity: RuleSeverity = RuleSeverity.HIGH
    ) -> None:
        """Register a rule requiring a numeric column to be non-negative.

        Args:
            column_name: The column that must never be negative.
            severity: The severity of a violation.
        """

        def _check(dataframe: pd.DataFrame) -> pd.Series:
            if column_name not in dataframe.columns:
                return pd.Series(False, index=dataframe.index)
            series = pd.to_numeric(dataframe[column_name], errors="coerce")
            return series < 0

        self.add_custom_rule(
            name=f"non_negative__{column_name}",
            description=f"Column '{column_name}' must not contain negative values.",
            validator=_check,
            severity=severity,
        )

    def add_date_order_rule(
        self,
        start_column: str,
        end_column: str,
        *,
        severity: RuleSeverity = RuleSeverity.MEDIUM,
    ) -> None:
        """Register a rule requiring ``start_column`` to not be after ``end_column``.

        Args:
            start_column: The earlier-expected date column (e.g. order date).
            end_column: The later-expected date column (e.g. delivery date).
            severity: The severity of a violation.
        """

        def _check(dataframe: pd.DataFrame) -> pd.Series:
            if start_column not in dataframe.columns or end_column not in dataframe.columns:
                return pd.Series(False, index=dataframe.index)
            start = pd.to_datetime(dataframe[start_column], errors="coerce")
            end = pd.to_datetime(dataframe[end_column], errors="coerce")
            return start.notna() & end.notna() & (start > end)

        self.add_custom_rule(
            name=f"date_order__{start_column}__before__{end_column}",
            description=(
                f"Column '{start_column}' must not be after '{end_column}'."
            ),
            validator=_check,
            severity=severity,
        )

    def add_primary_key_not_null_rule(
        self, column_name: str, *, severity: RuleSeverity = RuleSeverity.CRITICAL
    ) -> None:
        """Register a rule requiring a primary-key column to never be null.

        Args:
            column_name: The primary-key column.
            severity: The severity of a violation.
        """

        def _check(dataframe: pd.DataFrame) -> pd.Series:
            if column_name not in dataframe.columns:
                return pd.Series(False, index=dataframe.index)
            return dataframe[column_name].isna()

        self.add_custom_rule(
            name=f"primary_key_not_null__{column_name}",
            description=f"Column '{column_name}' (primary key) must not contain nulls.",
            validator=_check,
            severity=severity,
        )

    def add_unique_id_rule(
        self, column_name: str, *, severity: RuleSeverity = RuleSeverity.HIGH
    ) -> None:
        """Register a rule requiring an identifier column to contain no duplicates.

        Args:
            column_name: The identifier column that must be unique.
            severity: The severity of a violation.
        """

        def _check(dataframe: pd.DataFrame) -> pd.Series:
            if column_name not in dataframe.columns:
                return pd.Series(False, index=dataframe.index)
            series = dataframe[column_name]
            return series.notna() & series.duplicated(keep=False)

        self.add_custom_rule(
            name=f"unique_id__{column_name}",
            description=f"Column '{column_name}' must not contain duplicate values.",
            validator=_check,
            severity=severity,
        )

    # ------------------------------------------------------------------ #
    # Default ruleset construction
    # ------------------------------------------------------------------ #

    def build_default_ruleset(self, column_names: list[str]) -> "BusinessRuleValidator":
        """Register a heuristic, dataset-agnostic default ruleset.

        Inspects only column *names* (never dataset identity or hardcoded
        source-system knowledge) to register a reasonable starting set of
        rules: non-negativity for price/cost/quantity-like columns,
        not-null and uniqueness for identifier-like columns, and
        chronological ordering for recognizable start/end date pairs. This
        is a convenience baseline — callers with real domain knowledge
        should still add or override rules via the other ``add_*`` methods.

        Args:
            column_names: The column names present in the target dataset.

        Returns:
            ``self``, to allow fluent chaining, e.g.
            ``validator.build_default_ruleset(df.columns.tolist()).validate(df, ...)``.
        """
        lowered_lookup = {name: name.lower() for name in column_names}

        for name, lowered in lowered_lookup.items():
            if any(hint in lowered for hint in NON_NEGATIVE_NAME_HINTS):
                self.add_non_negative_rule(name)
            if any(hint in lowered for hint in ID_NAME_HINTS) or lowered == "id":
                self.add_primary_key_not_null_rule(name)
                self.add_unique_id_rule(name)

        start_candidates = [
            name
            for name, lowered in lowered_lookup.items()
            if any(hint in lowered for hint in START_DATE_NAME_HINTS)
        ]
        end_candidates = [
            name
            for name, lowered in lowered_lookup.items()
            if any(hint in lowered for hint in END_DATE_NAME_HINTS)
        ]
        for start_column in start_candidates:
            for end_column in end_candidates:
                self.add_date_order_rule(start_column, end_column)

        return self

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #

    def validate(
        self, dataframe: pd.DataFrame, *, dataset_name: str
    ) -> dict[str, Any]:
        """Evaluate every registered rule against the dataset.

        Args:
            dataframe: The dataset to validate.
            dataset_name: Human-readable dataset identifier, used for
                logging and embedded in the returned report.

        Returns:
            A JSON-serializable dictionary summarizing every rule's
            evaluation, including violation counts and a bounded sample of
            offending row indices.
        """
        row_count = dataframe.shape[0]
        rule_reports: list[dict[str, Any]] = []
        total_violations = 0

        for rule in self._rules:
            try:
                violation_mask = rule.validator(dataframe)
            except Exception as exc:  # noqa: BLE001 - one bad rule shouldn't break the run
                self._logger.log_operation(
                    dataset_name=dataset_name,
                    module=MODULE_NAME,
                    operation="rule_evaluation_error",
                    message=f"Rule '{rule.name}' raised an error: {exc}",
                    details={"rule_name": rule.name, "error": str(exc)},
                    level="error",
                )
                rule_reports.append(
                    {
                        "rule_name": rule.name,
                        "description": rule.description,
                        "severity": rule.severity.value,
                        "violation_count": None,
                        "violation_percentage": None,
                        "sample_violation_indices": [],
                        "error": str(exc),
                    }
                )
                continue

            violation_count = int(violation_mask.sum())
            total_violations += violation_count

            rule_reports.append(
                {
                    "rule_name": rule.name,
                    "description": rule.description,
                    "severity": rule.severity.value,
                    "violation_count": violation_count,
                    "violation_percentage": self._safe_percentage(
                        violation_count, row_count
                    ),
                    "sample_violation_indices": [
                        str(idx)
                        for idx in dataframe.index[violation_mask][
                            :MAX_SAMPLE_VIOLATIONS
                        ]
                    ],
                    "error": None,
                }
            )

        report = {
            "dataset_name": dataset_name,
            "rules_evaluated": len(self._rules),
            "total_violations": total_violations,
            "rules": rule_reports,
        }

        self._logger.log_operation(
            dataset_name=dataset_name,
            module=MODULE_NAME,
            operation="validate",
            message=(
                f"Evaluated {len(self._rules)} rule(s); "
                f"{total_violations} total violation(s)."
            ),
            details={
                "rules_evaluated": len(self._rules),
                "total_violations": total_violations,
            },
        )

        return report

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _safe_percentage(numerator: int, denominator: int) -> float:
        """Compute a percentage while guarding against division by zero."""
        if denominator <= 0:
            return 0.0
        return round((numerator / denominator) * 100, 2)


__all__ = ["BusinessRuleValidator", "ValidationRule", "RuleSeverity"]
