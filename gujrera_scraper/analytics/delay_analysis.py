"""Delay / timeline metrics (pure functions over ISO date strings)."""
from __future__ import annotations

from datetime import date


def _d(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s[:10]) if s else None
    except (TypeError, ValueError):
        return None


def days_between(a: str | None, b: str | None) -> int | None:
    da, db = _d(a), _d(b)
    return None if da is None or db is None else (db - da).days


def days_beyond(deadline: str | None, completed: bool, completion_filing_date: str | None, run_date: str) -> int | None:
    """Days past `deadline`: measured at the project-end filing date for completed projects, at run date otherwise.
    Returns 0 when not late, None when the deadline (or the completion date of a completed project) is unknown."""
    if not deadline:
        return None
    ref = completion_filing_date if completed else run_date
    if completed and not completion_filing_date:
        return None
    d = days_between(deadline, ref)
    return None if d is None else max(d, 0)


def months_between(a: str | None, b: str | None) -> float | None:
    d = days_between(a, b)
    return None if d is None else round(d / 30.4375, 1)


def timeline_elapsed_pct(start: str | None, end: str | None, run_date: str) -> float | None:
    """Share of the approved timeline (start .. latest approved completion) elapsed at run date, capped at 100."""
    total = days_between(start, end)
    done = days_between(start, run_date)
    if not total or total <= 0 or done is None:
        return None
    return round(max(0.0, min(100.0, done / total * 100)), 1)
