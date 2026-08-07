"""Comprehensive post-load database validation.

Provides :class:`DatabaseValidator`, which composes primary key integrity
checks (null keys, duplicate keys) with
:class:`~src.database.relationship_validator.RelationshipValidator`'s
foreign key referential integrity checks into a single, structured
validation report per table and for the database as a whole.

Typical usage:
    >>> from src.database.database_validator import DatabaseValidator
    >>> validator = DatabaseValidator(sql_executor)
    >>> report = validator.validate_all(schemas)
"""

from __future__ import annotations

from typing import Any

from src.database.relationship_validator import RelationshipValidator
from src.database.schema_builder import TableSchema
from src.database.sql_executor import SQLExecutor
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseValidator:
    """Validates primary key integrity and referential integrity across tables.

    Single responsibility: orchestrating and structuring validation results.
    The actual foreign-key orphan detection is delegated to
    :class:`~src.database.relationship_validator.RelationshipValidator`, so
    that logic exists in exactly one place.
    """

    def __init__(
        self,
        sql_executor: SQLExecutor,
        *,
        relationship_validator: RelationshipValidator | None = None,
    ) -> None:
        """Initialize the validator.

        Args:
            sql_executor: The statement-execution collaborator used to run
                primary-key null/duplicate queries.
            relationship_validator: The collaborator used for foreign key
                checks. If ``None``, a default
                :class:`~src.database.relationship_validator.RelationshipValidator`
                wrapping ``sql_executor`` is created.
        """
        self._executor = sql_executor
        self._relationship_validator = relationship_validator or RelationshipValidator(
            sql_executor
        )

    def validate_table(self, schema: TableSchema) -> dict[str, Any]:
        """Validate primary key integrity and referential integrity for one table.

        Args:
            schema: The table schema to validate.

        Returns:
            A JSON-serializable dictionary with ``table_name``,
            ``primary_key`` (null/duplicate counts and validity, or ``None``
            if the table has no primary key), ``foreign_keys`` (one entry
            per declared relationship), and an overall ``is_valid`` flag.
        """
        report: dict[str, Any] = {
            "table_name": schema.table_name,
            "primary_key": self._validate_primary_key(schema),
            "foreign_keys": [
                {
                    "column": result.column,
                    "referenced_table": result.referenced_table,
                    "referenced_column": result.referenced_column,
                    "orphan_row_count": result.orphan_row_count,
                    "is_valid": result.is_valid,
                }
                for result in self._relationship_validator.validate_table(schema)
            ],
        }

        primary_key_valid = report["primary_key"] is None or report["primary_key"]["is_valid"]
        foreign_keys_valid = all(fk["is_valid"] for fk in report["foreign_keys"])
        report["is_valid"] = primary_key_valid and foreign_keys_valid

        return report

    def validate_all(self, schemas: dict[str, TableSchema]) -> dict[str, Any]:
        """Validate every table in a schema set and summarize the outcome.

        Args:
            schemas: Every successfully materialized table's schema, keyed
                by table name.

        Returns:
            A dictionary with per-table validation reports under
            ``"tables"`` and an aggregate ``"overall_valid"`` flag.
        """
        table_reports = {
            table_name: self.validate_table(schema)
            for table_name, schema in schemas.items()
        }
        overall_valid = all(report["is_valid"] for report in table_reports.values())

        logger.info(
            "Database validation complete: %d table(s) checked, overall_valid=%s.",
            len(table_reports),
            overall_valid,
        )

        return {"tables": table_reports, "overall_valid": overall_valid}

    def _validate_primary_key(self, schema: TableSchema) -> dict[str, Any] | None:
        """Check a table's primary key column for nulls and duplicates.

        Args:
            schema: The table schema to check.

        Returns:
            A dictionary with ``column``, ``null_count``, ``duplicate_count``,
            and ``is_valid``, or ``None`` if the table declares no primary key.
        """
        if schema.primary_key is None:
            return None

        null_count = (
            self._executor.fetch_scalar(
                f'SELECT COUNT(*) FROM "{schema.table_name}" '
                f'WHERE "{schema.primary_key}" IS NULL;'
            )
            or 0
        )
        duplicate_count = (
            self._executor.fetch_scalar(
                f'SELECT COUNT(*) FROM ('
                f'    SELECT "{schema.primary_key}" FROM "{schema.table_name}" '
                f'    GROUP BY "{schema.primary_key}" HAVING COUNT(*) > 1'
                f");"
            )
            or 0
        )

        return {
            "column": schema.primary_key,
            "null_count": null_count,
            "duplicate_count": duplicate_count,
            "is_valid": null_count == 0 and duplicate_count == 0,
        }


__all__ = ["DatabaseValidator"]
