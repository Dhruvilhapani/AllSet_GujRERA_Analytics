"""Inventory (booked / unbooked) metrics.

Source: GujRERA block booking chart (booked / unbooked per block, updated through QPRs). The portal reports
*booked* units; this dataset therefore uses booked == sold and unbooked == unsold, and says so in every export.
"""
from __future__ import annotations

import pandas as pd


def sales_pct(booked, unbooked) -> float | None:
    if booked is None or unbooked is None or pd.isna(booked) or pd.isna(unbooked):
        return None
    total = booked + unbooked
    if total <= 0:
        return None
    return round(booked / total * 100, 2)


def aggregate(df: pd.DataFrame) -> dict:
    """Totals over projects that report a booking split (projects without data are excluded, not zeroed)."""
    have = df[df["booked_units"].notna() & df["unbooked_units"].notna()]
    booked = int(have["booked_units"].sum())
    unbooked = int(have["unbooked_units"].sum())
    total = booked + unbooked
    return {
        "projects_with_inventory_data": int(len(have)),
        "projects_without_inventory_data": int(len(df) - len(have)),
        "total_units_declared": int(df["total_units"].dropna().sum()),
        "booked_units": booked,
        "unbooked_units": unbooked,
        "sold_pct": round(booked / total * 100, 2) if total else None,
        "unsold_pct": round(unbooked / total * 100, 2) if total else None,
    }
