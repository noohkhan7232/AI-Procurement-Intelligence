"""Reusable, extensible file loading utilities.

Provides a single :class:`FileLoader` class responsible for safely reading
tabular data files into ``pandas.DataFrame`` objects, with:

    * Existence validation before any read attempt.
    * Encoding detection/fallback for CSV files.
    * Meaningful, typed exceptions instead of raw pandas/OS errors.
    * Structured logging of every load attempt (success and failure).
    * An architecture that cleanly extends to Excel, Parquet, and JSON
      without touching existing code (Open/Closed Principle).

Typical usage:
    >>> from src.utils.file_loader import FileLoader
    >>> loader = FileLoader()
    >>> df = loader.load_csv(Path("data/raw/vendors.csv"))
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.utils.logger import get_logger

logger = get_logger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Encodings attempted, in order, when reading CSV files.
CSV_ENCODING_FALLBACKS: tuple[str, ...] = ("utf-8", "utf-8-sig", "latin-1", "cp1252")

#: Default field separator used for CSV reads.
DEFAULT_CSV_SEPARATOR: str = ","


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class FileLoaderError(Exception):
    """Base exception for all file-loading failures raised by :class:`FileLoader`."""


class FileNotFoundInProjectError(FileLoaderError):
    """Raised when the requested file does not exist on disk."""


class EmptyFileError(FileLoaderError):
    """Raised when a file exists but contains no parsable data."""


class FileParsingError(FileLoaderError):
    """Raised when a file exists but could not be parsed into a DataFrame."""


class UnsupportedFileFormatError(FileLoaderError):
    """Raised when a requested load operation is not yet supported."""


# --------------------------------------------------------------------------- #
# File loader
# --------------------------------------------------------------------------- #


class FileLoader:
    """Load tabular data files into ``pandas.DataFrame`` objects safely.

    This class centralizes all file-reading logic for the platform so that
    downstream modules (profiling, quality checking, reporting) never touch
    raw I/O or pandas read functions directly. It currently supports CSV,
    with the architecture designed for straightforward extension to Excel,
    Parquet, and JSON via additional ``load_*`` methods that reuse the same
    validation/logging helpers.
    """

    def __init__(self) -> None:
        """Initialize the loader instance."""
        logger.debug("FileLoader initialized.")

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def load_csv(
        self,
        file_path: Path,
        *,
        separator: str = DEFAULT_CSV_SEPARATOR,
        encoding: str | None = None,
    ) -> pd.DataFrame:
        """Load a CSV file into a ``pandas.DataFrame``.

        Args:
            file_path: Path to the CSV file to load.
            separator: Field delimiter used in the file. Defaults to a comma.
            encoding: Explicit encoding to use. If ``None``, the loader
                attempts each encoding in :data:`CSV_ENCODING_FALLBACKS` in
                order until one succeeds.

        Returns:
            The loaded data as a ``pandas.DataFrame``.

        Raises:
            FileNotFoundInProjectError: If ``file_path`` does not exist.
            EmptyFileError: If the file exists but contains no data.
            FileParsingError: If the file could not be parsed with any
                attempted encoding.
        """
        self._validate_file_exists(file_path)

        encodings_to_try = (encoding,) if encoding else CSV_ENCODING_FALLBACKS
        last_error: Exception | None = None

        for candidate_encoding in encodings_to_try:
            try:
                logger.info(
                    "Loading CSV file '%s' with encoding='%s', separator='%s'.",
                    file_path,
                    candidate_encoding,
                    separator,
                )
                dataframe = pd.read_csv(
                    file_path, sep=separator, encoding=candidate_encoding
                )
            except pd.errors.EmptyDataError as exc:
                logger.error("File '%s' is empty: %s", file_path, exc)
                raise EmptyFileError(f"File is empty: {file_path}") from exc
            except (UnicodeDecodeError, pd.errors.ParserError) as exc:
                logger.warning(
                    "Failed to parse '%s' with encoding='%s': %s. Trying next "
                    "fallback encoding if available.",
                    file_path,
                    candidate_encoding,
                    exc,
                )
                last_error = exc
                continue
            else:
                logger.info(
                    "Successfully loaded '%s' -> shape=%s.",
                    file_path,
                    dataframe.shape,
                )
                return dataframe

        logger.error(
            "Exhausted all encodings %s while parsing '%s'.",
            encodings_to_try,
            file_path,
        )
        raise FileParsingError(
            f"Could not parse CSV file '{file_path}' using any of the "
            f"attempted encodings {encodings_to_try}."
        ) from last_error

    def load_excel(self, file_path: Path, **kwargs: object) -> pd.DataFrame:
        """Load an Excel workbook into a ``pandas.DataFrame``.

        Args:
            file_path: Path to the ``.xlsx``/``.xls`` file.
            **kwargs: Additional keyword arguments forwarded to
                ``pandas.read_excel`` (e.g. ``sheet_name``).

        Returns:
            The loaded data as a ``pandas.DataFrame``.

        Raises:
            FileNotFoundInProjectError: If ``file_path`` does not exist.
            FileParsingError: If the file could not be parsed.
        """
        self._validate_file_exists(file_path)
        try:
            logger.info("Loading Excel file '%s'.", file_path)
            dataframe = pd.read_excel(file_path, **kwargs)
        except ValueError as exc:
            logger.error("Failed to parse Excel file '%s': %s", file_path, exc)
            raise FileParsingError(
                f"Could not parse Excel file '{file_path}': {exc}"
            ) from exc
        logger.info(
            "Successfully loaded '%s' -> shape=%s.", file_path, dataframe.shape
        )
        return dataframe

    def load_parquet(self, file_path: Path, **kwargs: object) -> pd.DataFrame:
        """Load a Parquet file into a ``pandas.DataFrame``.

        Args:
            file_path: Path to the ``.parquet`` file.
            **kwargs: Additional keyword arguments forwarded to
                ``pandas.read_parquet``.

        Returns:
            The loaded data as a ``pandas.DataFrame``.

        Raises:
            FileNotFoundInProjectError: If ``file_path`` does not exist.
            FileParsingError: If the file could not be parsed.
        """
        self._validate_file_exists(file_path)
        try:
            logger.info("Loading Parquet file '%s'.", file_path)
            dataframe = pd.read_parquet(file_path, **kwargs)
        except Exception as exc:  # pandas raises varied backend-specific errors
            logger.error("Failed to parse Parquet file '%s': %s", file_path, exc)
            raise FileParsingError(
                f"Could not parse Parquet file '{file_path}': {exc}"
            ) from exc
        logger.info(
            "Successfully loaded '%s' -> shape=%s.", file_path, dataframe.shape
        )
        return dataframe

    def load_json(self, file_path: Path, **kwargs: object) -> pd.DataFrame:
        """Load a JSON file into a ``pandas.DataFrame``.

        Args:
            file_path: Path to the ``.json`` file.
            **kwargs: Additional keyword arguments forwarded to
                ``pandas.read_json``.

        Returns:
            The loaded data as a ``pandas.DataFrame``.

        Raises:
            FileNotFoundInProjectError: If ``file_path`` does not exist.
            FileParsingError: If the file could not be parsed.
        """
        self._validate_file_exists(file_path)
        try:
            logger.info("Loading JSON file '%s'.", file_path)
            dataframe = pd.read_json(file_path, **kwargs)
        except ValueError as exc:
            logger.error("Failed to parse JSON file '%s': %s", file_path, exc)
            raise FileParsingError(
                f"Could not parse JSON file '{file_path}': {exc}"
            ) from exc
        logger.info(
            "Successfully loaded '%s' -> shape=%s.", file_path, dataframe.shape
        )
        return dataframe

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _validate_file_exists(file_path: Path) -> None:
        """Ensure the given path exists and is a regular file.

        Args:
            file_path: The path to validate.

        Raises:
            FileNotFoundInProjectError: If the path does not exist or is not
                a regular file.
        """
        if not file_path.exists():
            logger.error("File not found: '%s'.", file_path)
            raise FileNotFoundInProjectError(f"File not found: {file_path}")
        if not file_path.is_file():
            logger.error("Path exists but is not a file: '%s'.", file_path)
            raise FileNotFoundInProjectError(f"Path is not a file: {file_path}")


__all__ = [
    "FileLoader",
    "FileLoaderError",
    "FileNotFoundInProjectError",
    "EmptyFileError",
    "FileParsingError",
    "UnsupportedFileFormatError",
]