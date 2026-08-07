"""Enterprise database architecture and ETL package.

Exposes the primary ETL orchestrator and every specialized database module
as top-level imports for convenience:

    >>> from src.database import ETLPipeline, DatabaseManager
"""

from src.database.connection import DatabaseConnectionManager
from src.database.data_loader import DataLoader, DataLoadError
from src.database.database_manager import DatabaseManager
from src.database.database_validator import DatabaseValidator
from src.database.etl_pipeline import ETLPipeline
from src.database.relationship_validator import RelationshipValidator
from src.database.schema_builder import SchemaBuilder, TableSchema
from src.database.sql_executor import SQLExecutor, SQLExecutionError
from src.database.transaction_manager import TransactionManager

__all__ = [
    "ETLPipeline",
    "DatabaseConnectionManager",
    "DatabaseManager",
    "DatabaseValidator",
    "RelationshipValidator",
    "SchemaBuilder",
    "TableSchema",
    "SQLExecutor",
    "SQLExecutionError",
    "TransactionManager",
    "DataLoader",
    "DataLoadError",
]
