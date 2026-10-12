"""Developer (promoter account) level metrics.

Unit of analysis = GujRERA promoter account (developer_id = promoterId). Accounts that share a PAN hash
(same legal person) are additionally rolled up in `developer_group_metrics` (see developer_groups table);
name-similarity groupings are review-only and never merged.
"""
from __future__ import annotations

import pandas as pd


def _pct(a, b):
    return round(a / b * 100, 2) if b else None


def metrics_for(g: pd.DataFrame) -> dict:
    n = len(g)
    status = g["status_derived"].fillna("unknown")
    crawled = g[g["detail_crawled"] == 1]
    comp = g["completion_pct"].dropna()
    units = g["total_units"].fillna(0)
    wc = g[g["completion_pct"].notna() & g["total_units"].notna() & (g["total_units"] > 0)]
    inv = g[g["booked_units"].notna() & g["unbooked_units"].notna()]
    booked, unbooked = int(inv["booked_units"].sum()), int(inv["unbooked_units"].sum())
    late = g["days_beyond_current"].dropna()
    late_pos = late[late > 0]
    ext = g["extension_count"].dropna()
    counts = {s: int((status == s).sum()) for s in ["completed", "ongoing", "extended", "overdue",
                                                    "cancelled_revoked", "unknown"]}
    m = {
        "total_projects": n,
        "residential_projects": int((g["project_category"] == "residential").sum()),
        "mixed_projects": int((g["project_category"] == "mixed").sum()),
        **{f"{k}_projects": v for k, v in counts.items()},
        "active_projects": counts["ongoing"] + counts["extended"] + counts["overdue"],
        "projects_detail_crawled": int(len(crawled)),
        "completion_rate_pct": _pct(counts["completed"], n),
        "avg_completion_pct": round(float(comp.mean()), 2) if len(comp) else None,
        "median_completion_pct": round(float(comp.median()), 2) if len(comp) else None,
        "units_weighted_completion_pct": round(float((wc["completion_pct"] * wc["total_units"]).sum()
                                                     / wc["total_units"].sum()), 2) if len(wc) else None,
        "projects_progress_lt_25": int((comp < 25).sum()),
        "projects_progress_25_50": int(((comp >= 25) & (comp < 50)).sum()),
        "projects_progress_50_75": int(((comp >= 50) & (comp < 75)).sum()),
        "projects_progress_ge_75": int((comp >= 75).sum()),
        "total_units": int(units.sum()),
        "booked_units": booked, "unbooked_units": unbooked,
        "sold_pct": _pct(booked, booked + unbooked), "unsold_pct": _pct(unbooked, booked + unbooked),
        "projects_past_promised_completion": int(len(late_pos)),
        "avg_delay_days_of_late_projects": round(float(late_pos.mean()), 1) if len(late_pos) else None,
        "max_delay_days": int(late.max()) if len(late) else None,
        "projects_with_extensions": int((ext > 0).sum()),
        "total_extensions": int(ext.sum()),
        "projects_with_repeated_extensions": int((ext >= 2).sum()),
        "total_extension_days": int(g["total_extension_days"].dropna().sum()),
        "complaints_total": _sum(crawled, "complaints_total"),
        "complaints_pending": _sum(crawled, "complaints_pending"),
        "complaints_resolved": _sum(crawled, "complaints_resolved"),
        "complaint_to_project_ratio": round(_sum(crawled, "complaints_total") / len(crawled), 3) if len(crawled) else None,
        "projects_with_complaints": int((crawled["complaints_total"].fillna(0) > 0).sum()),
        "suo_motu_total": _sum(crawled, "suo_motu_total"),
        "projects_with_authority_actions": int(((crawled["suo_motu_total"].fillna(0) > 0)
                                                | (crawled["challans_total"].fillna(0) > 0)).sum()),
        "projects_with_penalties_or_orders": int(((crawled["challans_total"].fillna(0) > 0)
                                                  | (crawled["suo_motu_penalty_orders"].fillna(0) > 0)
                                                  | (crawled["complaint_penalty_orders"].fillna(0) > 0)).sum()),
        "challans_total": _sum(crawled, "challans_total"),
        "challan_amount_total": float(crawled["challan_amount_total"].fillna(0).sum()),
        "qpr_defaults": _sum(crawled, "qpr_defaults"),
    }
    return m


def _sum(df: pd.DataFrame, col: str) -> int:
    return int(df[col].fillna(0).sum()) if col in df else 0


def compute(projects: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for dev_id, g in projects[projects["developer_id"].notna()].groupby("developer_id"):
        rows.append({"developer_id": int(dev_id), **metrics_for(g)})
    return pd.DataFrame(rows)


def compute_groups(projects: pd.DataFrame, groups: pd.DataFrame) -> pd.DataFrame:
    """Roll-up for PAN-confirmed groups of promoter accounts (same legal person)."""
    conf = groups[groups["match_basis"] == "pan_hash"]
    rows = []
    for gid, gg in conf.groupby("group_id"):
        ids = set(gg["developer_id"].astype(int))
        sub = projects[projects["developer_id"].isin(ids)]
        if len(sub):
            rows.append({"group_id": gid, "developer_ids": ",".join(map(str, sorted(ids))), **metrics_for(sub)})
    return pd.DataFrame(rows)
