"""Automatic SQLite schema derivation from cleaned pandas DataFrames.

Provides :class:`SchemaBuilder`, which inspects a cleaned DataFrame and
derives a complete :class:`TableSchema`: SQLite column types, a primary key
(detected heuristically from uniqueness and naming), and — once every
dataset's schema has been built — foreign key relationships inferred purely
by matching an ``*_id``-style column name against another table's declared
primary key. No dataset or column name is ever hardcoded; every decision is
driven by the data and by generic naming conventions.

Typical usage:
    >>> from src.database.schema_builder import SchemaBuilder
    >>> builder = SchemaBuilder()
    >>> schemas = {
    ...     "customers": builder.build_schema(customers_df, table_name="customers"),
    ...     "orders": builder.build_schema(orders_df, table_name="orders"),
    ... }
    >>> builder.infer_foreign_keys(schemas)
    >>> sql = builder.generate_create_table_sql(schemas["orders"])
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from src.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: A column qualifies as a primary-key candidate only when every value is
#: distinct (uniqueness ratio == 1.0) and no value is missing.
PRIMARY_KEY_UNIQUENESS_THRESHOLD: float = 1.0

#: SQLite storage classes this builder emits. SQLite does not have a native
#: BOOLEAN or DATETIME type; booleans are stored as 0/1 INTEGER and dates as
#: ISO-8601 TEXT, both SQLite-idiomatic and fully queryable/sortable.
SQL_TYPE_INTEGER: str = "INTEGER"
SQL_TYPE_REAL: str = "REAL"
SQL_TYPE_TEXT: str = "TEXT"


@dataclass
class ColumnDefinition:
    """A single column's derived SQLite definition.

    Attributes:
        name: The column name (assumed already snake_case; the cleaning
            framework standardizes this before data reaches this module).
        sql_type: The SQLite storage class (``INTEGER``, ``REAL``, ``TEXT``).
        is_nullable: Whether the source data contains any missing values in
            this column.
        is_primary_key: Whether this column was selected as the table's
            primary key.
    """

    name: str
    sql_type: str
    is_nullable: bool = True
    is_primary_key: bool = False


@dataclass
class ForeignKeyDefinition:
    """A single inferred foreign key relationship.

    Attributes:
        column: The column in the referencing (child) table.
        referenced_table: The name of the referenced (parent) table.
        referenced_column: The primary key column in the parent table.
    """

    column: str
    referenced_table: str
    referenced_column: str


@dataclass
class TableSchema:
    """The complete derived schema for a single table.

    Attributes:
        table_name: The table name.
        columns: Every column's derived definition, in source column order.
        primary_key: The name of the primary key column, or ``None`` if no
            suitable column was detected.
        foreign_keys: Every inferred foreign key relationship for this
            table. Populated by :meth:`SchemaBuilder.infer_foreign_keys`,
            empty immediately after :meth:`SchemaBuilder.build_schema`.
    """

    table_name: str
    columns: list[ColumnDefinition] = field(default_factory=list)
    primary_key: str | None = None
    foreign_keys: list[ForeignKeyDefinition] = field(default_factory=list)

    def column_names(self) -> list[str]:
        """Return every column name, in schema order.

        Returns:
            A list of column names.
        """
        return [column.name for column in self.columns]


class SchemaBuilder:
    """Derives SQLite table schemas from DataFrames and renders their DDL.

    Single responsibility: schema derivation and SQL generation. Executing
    the generated statements is the responsibility of
    :class:`~src.database.database_manager.DatabaseManager`.
    """

    def build_schema(self, dataframe: pd.DataFrame, *, table_name: str) -> TableSchema:
        """Derive a table's column definitions and primary key from its data.

        Foreign keys are intentionally left empty here — they require
        knowledge of every other table's primary key and are populated in a
        second pass by :meth:`infer_foreign_keys`.

        Args:
            dataframe: The cleaned dataset to derive a schema from.
            table_name: The name of the resulting table.

        Returns:
            The derived :class:`TableSchema`, without foreign keys.
        """
        columns = [self._build_column(dataframe, name) for name in dataframe.columns]
        primary_key = self._detect_primary_key(dataframe, table_name)

        for column in columns:
            if column.name == primary_key:
                column.is_primary_key = True
                column.is_nullable = False

        schema = TableSchema(table_name=table_name, columns=columns, primary_key=primary_key)
        logger.info(
            "Built schema for table '%s': %d column(s), primary_key=%s.",
            table_name,
            len(columns),
            primary_key,
        )
        return schema

    def infer_foreign_keys(self, schemas: dict[str, TableSchema]) -> None:
        """Infer and attach foreign keys across a complete set of table schemas.

        A column ``c`` in table ``child`` is inferred as a foreign key to
        table ``parent`` when: ``c`` looks like an identifier column (ends
        with ``_id`` or is literally named ``id``), ``c`` is not ``child``'s
        own primary key, and some other table ``parent`` declares its
        primary key as a column also named ``c``.

        Mutates every schema in ``schemas`` in place by appending discovered
        :class:`ForeignKeyDefinition` entries.

        Args:
            schemas: Every table's schema, keyed by table name. Must already
                have primary keys populated via :meth:`build_schema`.
        """
        tables_by_primary_key: dict[str, list[str]] = {}
        for table_name, schema in schemas.items():
            if schema.primary_key:
                tables_by_primary_key.setdefault(schema.primary_key, []).append(
                    table_name
                )

        for table_name, schema in schemas.items():
            for column in schema.columns:
                if column.name == schema.primary_key:
                    continue
                if not self._looks_like_id_column(column.name):
                    continue

                for referenced_table in tables_by_primary_key.get(column.name, []):
                    if referenced_table == table_name:
                        continue
                    schema.foreign_keys.append(
                        ForeignKeyDefinition(
                            column=column.name,
                            referenced_table=referenced_table,
                            referenced_column=column.name,
                        )
                    )
                    logger.info(
                        "Inferred foreign key: %s.%s -> %s.%s.",
                        table_name,
                        column.name,
                        referenced_table,
                        column.name,
                    )

    def generate_create_table_sql(self, schema: TableSchema) -> str:
        """Render the ``CREATE TABLE`` statement for a schema.

        Args:
            schema: The table schema to render.

        Returns:
            A complete ``CREATE TABLE IF NOT EXISTS`` statement, including
            column constraints and foreign key clauses.
        """
        column_clauses = [self._render_column(column) for column in schema.columns]
        for fk in schema.foreign_keys:
            column_clauses.append(
                f'FOREIGN KEY ("{fk.column}") REFERENCES "{fk.referenced_table}" '
                f'("{fk.referenced_column}")'
            )

        columns_sql = ",\n    ".join(column_clauses)
        return f'CREATE TABLE IF NOT EXISTS "{schema.table_name}" (\n    {columns_sql}\n);'

    def generate_index_sql(self, schema: TableSchema) -> list[str]:
        """Render ``CREATE INDEX`` statements for a schema's foreign key columns.

        Args:
            schema: The table schema to render indexes for.

        Returns:
            One ``CREATE INDEX IF NOT EXISTS`` statement per foreign key
            column, accelerating join and referential-integrity lookups.
        """
        statements: list[str] = []
        for fk in schema.foreign_keys:
            index_name = f"idx_{schema.table_name}_{fk.column}"
            statements.append(
                f'CREATE INDEX IF NOT EXISTS "{index_name}" '
                f'ON "{schema.table_name}" ("{fk.column}");'
            )
        return statements

    # ------------------------------------------------------------------ #
    # Column derivation
    # ------------------------------------------------------------------ #

    def _build_column(self, dataframe: pd.DataFrame, column_name: str) -> ColumnDefinition:
        """Derive a single column's SQLite definition from its data.

        Args:
            dataframe: The dataset the column belongs to.
            column_name: The column to derive a definition for.

        Returns:
            The derived :class:`ColumnDefinition` (primary-key flag not yet
            set; applied afterward in :meth:`build_schema`).
        """
        series = dataframe[column_name]
        return ColumnDefinition(
            name=str(column_name),
            sql_type=self._map_dtype(series),
            is_nullable=bool(series.isna().any()),
        )

    @staticmethod
    def _map_dtype(series: pd.Series) -> str:
        """Map a pandas dtype to a SQLite storage class.

        Args:
            series: The column to map.

        Returns:
            One of :data:`SQL_TYPE_INTEGER`, :data:`SQL_TYPE_REAL`, or
            :data:`SQL_TYPE_TEXT`.
        """
        if pd.api.types.is_bool_dtype(series):
            return SQL_TYPE_INTEGER
        if pd.api.types.is_integer_dtype(series):
            return SQL_TYPE_INTEGER
        if pd.api.types.is_float_dtype(series):
            return SQL_TYPE_REAL
        # Datetime, category, and plain object/string columns are all
        # stored as TEXT: SQLite has no native datetime type, and category
        # values must round-trip as their original string representation.
        return SQL_TYPE_TEXT

    # ------------------------------------------------------------------ #
    # Primary key detection
    # ------------------------------------------------------------------ #

    def _detect_primary_key(
        self, dataframe: pd.DataFrame, table_name: str
    ) -> str | None:
        """Heuristically select a primary key column from the dataset.

        A column qualifies as a candidate when it has zero missing values
        and every value is distinct. Among candidates, preference is given
        to a column literally named ``"{table_name}_id"``, then ``"id"``,
        then any other identifier-like column name, then falls back to the
        first qualifying candidate in column order.

        Args:
            dataframe: The dataset to inspect.
            table_name: The table's name, used to check for a
                ``"{table_name}_id"``-style column.

        Returns:
            The selected primary key column name, or ``None`` if no column
            qualifies (e.g. an empty dataset, or no fully unique column).
        """
        row_count = dataframe.shape[0]
        if row_count == 0:
            return None

        candidates = [
            str(column_name)
            for column_name in dataframe.columns
            if not dataframe[column_name].isna().any()
            and (dataframe[column_name].nunique(dropna=True) / row_count)
            >= PRIMARY_KEY_UNIQUENESS_THRESHOLD
        ]
        if not candidates:
            return None

        preferred_name = f"{table_name}_id"
        if preferred_name in candidates:
            return preferred_name
        if "id" in candidates:
            return "id"

        id_like_candidates = [c for c in candidates if self._looks_like_id_column(c)]
        if id_like_candidates:
            return id_like_candidates[0]

        return candidates[0]

    @staticmethod
    def _looks_like_id_column(column_name: str) -> bool:
        """Check whether a column name follows common identifier conventions.

        Args:
            column_name: The column name to check.

        Returns:
            ``True`` if the name is exactly ``"id"`` or ends with ``"_id"``.
        """
        lowered = column_name.lower()
        return lowered == "id" or lowered.endswith("_id")

    # ------------------------------------------------------------------ #
    # Rendering
    # ------------------------------------------------------------------ #

    @staticmethod
    def _render_column(column: ColumnDefinition) -> str:
        """Render a single column's clause within a ``CREATE TABLE`` statement.

        Args:
            column: The column definition to render.

        Returns:
            The column's DDL clause, e.g. ``"order_id" INTEGER PRIMARY KEY``.
        """
        clause_parts = [f'"{column.name}"', column.sql_type]
        if column.is_primary_key:
            clause_parts.append("PRIMARY KEY")
        elif not column.is_nullable:
            clause_parts.append("NOT NULL")
        return " ".join(clause_parts)


__all__ = ["SchemaBuilder", "TableSchema", "ColumnDefinition", "ForeignKeyDefinition"]
