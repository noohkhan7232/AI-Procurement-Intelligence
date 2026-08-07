"""Foreign key referential integrity validation.

Provides :class:`RelationshipValidator`, which checks — for every foreign
key declared on a table — whether every non-null value in the child column
actually exists in the referenced parent table's key column. This is a
data-level check independent of SQLite's own ``PRAGMA foreign_keys``
enforcement, and is what powers the platform's Validation Report: it
reports *how many* orphan rows exist per relationship, not just whether any
exist.

Typical usage:
    >>> from src.database.relationship_validator import RelationshipValidator
    >>> validator = RelationshipValidator(sql_executor)
    >>> results = validator.validate_table(schema)
"""

from __future__ import annotations

from dataclasses import dataclass

from src.database.schema_builder import ForeignKeyDefinition, TableSchema
from src.database.sql_executor import SQLExecutor
from src.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ForeignKeyValidationResult:
    """The outcome of validating a single foreign key relationship.

    Attributes:
        table_name: The child (referencing) table.
        column: The child table's foreign key column.
        referenced_table: The parent (referenced) table.
        referenced_column: The parent table's key column.
        orphan_row_count: Number of rows in the child table whose foreign
            key value is non-null but does not exist in the parent table.
        is_valid: Whether the relationship has zero orphan rows.
    """

    table_name: str
    column: str
    referenced_table: str
    referenced_column: str
    orphan_row_count: int
    is_valid: bool


class RelationshipValidator:
    """Validates referential integrity for a table's declared foreign keys.

    Single responsibility: foreign key orphan detection only. Primary key
    null/duplicate checks live in
    :class:`~src.database.database_validator.DatabaseValidator`, which
    composes this class for the referential-integrity portion of its report.
    """

    def __init__(self, sql_executor: SQLExecutor) -> None:
        """Initialize the validator.

        Args:
            sql_executor: The statement-execution collaborator used to run
                orphan-detection queries.
        """
        self._executor = sql_executor

    def validate_table(self, schema: TableSchema) -> list[ForeignKeyValidationResult]:
        """Validate every foreign key declared on a table.

        Args:
            schema: The table schema whose foreign keys should be checked.

        Returns:
            One :class:`ForeignKeyValidationResult` per foreign key declared
            on the table. Empty if the table declares no foreign keys.
        """
        results = [
            self._validate_foreign_key(schema.table_name, fk)
            for fk in schema.foreign_keys
        ]
        return results

    def _validate_foreign_key(
        self, table_name: str, foreign_key: ForeignKeyDefinition
    ) -> ForeignKeyValidationResult:
        """Count orphan rows for a single foreign key relationship.

        Args:
            table_name: The child table's name.
            foreign_key: The foreign key relationship to validate.

        Returns:
            The validation result for this relationship.
        """
        orphan_count = self._count_orphans(table_name, foreign_key)
        is_valid = orphan_count == 0

        if not is_valid:
            logger.warning(
                "Referential integrity violation: %s.%s has %d orphan row(s) "
                "not present in %s.%s.",
                table_name,
                foreign_key.column,
                orphan_count,
                foreign_key.referenced_table,
                foreign_key.referenced_column,
            )

        return ForeignKeyValidationResult(
            table_name=table_name,
            column=foreign_key.column,
            referenced_table=foreign_key.referenced_table,
            referenced_column=foreign_key.referenced_column,
            orphan_row_count=orphan_count,
            is_valid=is_valid,
        )

    def _count_orphans(self, table_name: str, foreign_key: ForeignKeyDefinition) -> int:
        """Count child rows whose foreign key value has no matching parent row.

        Args:
            table_name: The child table's name.
            foreign_key: The foreign key relationship to check.

        Returns:
            The number of orphan rows. Rows where the foreign key column is
            ``NULL`` are not counted as orphans (an absent reference is not
            a broken reference).
        """
        query = (
            f'SELECT COUNT(*) FROM "{table_name}" AS child '
            f'WHERE child."{foreign_key.column}" IS NOT NULL '
            f'AND NOT EXISTS ('
            f'    SELECT 1 FROM "{foreign_key.referenced_table}" AS parent '
            f'    WHERE parent."{foreign_key.referenced_column}" = child."{foreign_key.column}"'
            f");"
        )
        return self._executor.fetch_scalar(query) or 0


__all__ = ["RelationshipValidator", "ForeignKeyValidationResult"]
