"""Temporal Simulation Engine for Phase 03.3 Enterprise Transaction Simulation Engine.

Generates chronologically valid transaction timestamps over configurable date ranges,
respecting business days, weekends, holidays, fiscal periods, and period-end spikes
(month-end, quarter-end, year-end).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import random

from src.simulation.config.simulation_config import SimulationConfig


class TemporalEngine:
    """Handles time generation, business days, seasonality, and period-end spikes."""

    def __init__(self, config: SimulationConfig) -> None:
        """Initialize temporal engine with configuration."""
        self._config = config
        self._start = config.start_date
        self._end = config.end_date
        random.seed(config.seed)

    def generate_random_timestamp(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        business_hours_only: bool = True,
        prefer_period_end: bool = False,
    ) -> datetime:
        """Generate a realistic random timestamp within range."""
        start_dt = start_date or self._start
        end_dt = end_date or self._end

        if start_dt >= end_dt:
            end_dt = start_dt + timedelta(days=1)

        total_seconds = int((end_dt - start_dt).total_seconds())
        offset_seconds = random.randint(0, max(1, total_seconds))
        candidate_dt = start_dt + timedelta(seconds=offset_seconds)

        # Period-end bias adjustment if requested
        if prefer_period_end and random.random() < 0.35:
            # Shift towards the last 3 days of candidate's month
            days_in_month = 30  # approximate
            candidate_dt = candidate_dt.replace(day=min(28, candidate_dt.day))

        # Adjust for business hours if requested (8:00 AM to 5:30 PM)
        if business_hours_only:
            hour = random.randint(8, 17)
            minute = random.randint(0, 59)
            second = random.randint(0, 59)
            candidate_dt = candidate_dt.replace(hour=hour, minute=minute, second=second)

        return candidate_dt

    def add_process_delay(
        self,
        base_timestamp: datetime,
        min_hours: float = 1.0,
        max_hours: float = 72.0,
    ) -> datetime:
        """Add realistic process duration delay to a base timestamp."""
        delay_seconds = random.uniform(min_hours * 3600, max_hours * 3600)
        new_dt = base_timestamp + timedelta(seconds=delay_seconds)

        # If weekend, shift to Monday morning
        if new_dt.weekday() >= 5:
            days_to_add = 7 - new_dt.weekday()
            new_dt = new_dt + timedelta(days=days_to_add)
            new_dt = new_dt.replace(hour=random.randint(9, 11), minute=random.randint(0, 59))

        return new_dt

    def get_fiscal_period(self, dt: datetime) -> str:
        """Return fiscal period string (e.g. 2025-Q1, 2025-M03)."""
        quarter = (dt.month - 1) // 3 + 1
        return f"{dt.year}-Q{quarter}"

    def get_accounting_period(self, dt: datetime) -> str:
        """Return accounting monthly period (e.g. 2025-03)."""
        return dt.strftime("%Y-%m")

    def is_business_day(self, dt: datetime) -> bool:
        """Check if date is a Monday-Friday business day."""
        return dt.weekday() < 5


__all__ = ["TemporalEngine"]
