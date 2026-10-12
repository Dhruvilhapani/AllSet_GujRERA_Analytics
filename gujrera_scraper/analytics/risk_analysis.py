"""Analytical Risk Score (NOT an official RERA rating) — see docs/risk_methodology.md and config/risk_scoring.yaml.

Each component is 0..100. Components without data for a project are skipped and the remaining weights are
re-normalised; `score_coverage` = share of total weight that had data. Every flag carries its evidence.
"""
from __future__ import annotations

import math

import pandas as pd


def _num(v):
    if v is None:
        return None
    try:
        if isinstance(v, float) and math.isnan(v):
            return None
    except TypeError:
        return None
    return v


def components(r: dict, cfg: dict) -> dict[str, float | None]:
    c = cfg["components"]
    completed = r.get("status_derived") == "completed"
    crawled = bool(r.get("detail_crawled"))
    out: dict[str, float | None] = {}

    d = _num(r.get("days_beyond_current"))
    out["delay"] = None if d is None else min(100.0, d / c["delay"]["full_scale_days"] * 100)

    e = _num(r.get("extension_count"))
    out["extensions"] = None if e is None or not crawled else min(100.0, e * c["extensions"]["points_per_extension"])

    if crawled:
        tot, pend = _num(r.get("complaints_total")) or 0, _num(r.get("complaints_pending")) or 0
        resolved_or_unknown = tot - pend
        pts = c["complaints"]["points_per_complaint"]
        out["complaints"] = min(100.0, resolved_or_unknown * pts + pend * pts * c["complaints"]["pending_multiplier"])
        sm = _num(r.get("suo_motu_total")) or 0
        pen = (_num(r.get("suo_motu_penalty_orders")) or 0) + (_num(r.get("complaint_penalty_orders")) or 0) \
            + (_num(r.get("challans_total")) or 0)
        out["regulatory"] = min(100.0, sm * c["regulatory"]["points_per_suo_motu"]
                                + pen * c["regulatory"]["points_per_penalty_order"])
    else:
        out["complaints"] = out["regulatory"] = None

    if completed:
        out["completion"] = 0.0
        out["stagnation"] = 0.0
    else:
        el, cp = _num(r.get("timeline_elapsed_pct")), _num(r.get("completion_pct"))
        out["completion"] = None if el is None or cp is None else \
            max(0.0, min(100.0, (el - cp) / c["completion"]["full_scale_gap_pp"] * 100))
        st = _num(r.get("stalled_flag"))
        ms = _num(r.get("months_since_last_qpr"))
        if st is None and ms is None:
            out["stagnation"] = None
        elif st:
            out["stagnation"] = float(c["stagnation"]["stalled_score"])
        else:
            g, f = c["stagnation"]["grace_months"], c["stagnation"]["full_scale_months"]
            out["stagnation"] = 0.0 if ms is None else max(0.0, min(100.0, (ms - g) / (f - g) * 100))

    us = _num(r.get("unsold_pct"))
    if us is None:
        out["inventory"] = None
    else:
        cp = _num(r.get("completion_pct"))
        early = not completed and (cp is None or cp < 75)
        out["inventory"] = us * (c["inventory"]["early_stage_factor"] if early else 1.0)
    return {k: (None if v is None else round(float(v), 1)) for k, v in out.items()}


def score(comps: dict[str, float | None], weights: dict[str, float]) -> tuple[float | None, float]:
    total_w = sum(w for w in weights.values() if w > 0)
    have = {k: w for k, w in weights.items() if w > 0 and comps.get(k) is not None}
    hw = sum(have.values())
    if hw == 0:
        return None, 0.0
    s = sum(comps[k] * w for k, w in have.items()) / hw
    return round(s, 1), round(hw / total_w, 2)


def flags(r: dict, cfg: dict) -> list[dict]:
    f = cfg["flags"]
    out = []
    completed = r.get("status_derived") == "completed"
    d = _num(r.get("days_beyond_current"))
    if not completed and d is not None and d >= f["severe_delay_days"]:
        out.append({"flag": "severe_delay", "evidence": f"{int(d)} days past latest approved completion date "
                    f"{r.get('current_completion_date')} with no project-end (Q-E/Form 4) filing"})
    e = _num(r.get("extension_count"))
    if e is not None and e >= f["repeated_extension_count"]:
        out.append({"flag": "repeated_extension", "evidence": f"{int(e)} extensions; original completion "
                    f"{r.get('original_completion_date')} -> current {r.get('current_completion_date')}"})
    cp, el = _num(r.get("completion_pct")), _num(r.get("timeline_elapsed_pct"))
    if not completed and cp is not None and el is not None and cp < f["low_progress_pct"] \
            and el >= f["low_progress_min_elapsed_pct"]:
        out.append({"flag": "low_construction_progress", "evidence": f"QPR progress {cp}% ({r.get('latest_progress_quarter')}) "
                    f"with {el}% of approved timeline elapsed"})
    ct = _num(r.get("complaints_total"))
    if ct is not None and ct >= f["high_complaints"]:
        out.append({"flag": "high_complaints", "evidence": f"{int(ct)} complaints linked "
                    f"({int(_num(r.get('complaints_pending')) or 0)} pending)"})
    sm, ch = _num(r.get("suo_motu_total")) or 0, _num(r.get("challans_total")) or 0
    pen = (_num(r.get("suo_motu_penalty_orders")) or 0) + (_num(r.get("complaint_penalty_orders")) or 0)
    if sm or ch or pen:
        amt = _num(r.get("challan_amount_total")) or 0
        out.append({"flag": "regulatory_action", "evidence": f"{int(sm)} suo-motu proceeding(s), {int(pen)} order(s) "
                    f"with penalty, {int(ch)} challan(s) totalling Rs {amt:,.0f}"})
    if _num(r.get("stalled_flag")):
        out.append({"flag": "stalled_progress", "evidence": f"latest progress {cp}% ; months since last QPR "
                    f"{r.get('months_since_last_qpr')} ; velocity {r.get('progress_velocity_pp_per_qtr')} pp/quarter"})
    us = _num(r.get("unsold_pct"))
    if us is not None and us >= f["high_unsold_pct"] and (completed or (cp or 0) >= f["high_unsold_min_progress_pct"]):
        out.append({"flag": "high_unsold_inventory", "evidence": f"{us}% unbooked ({r.get('unbooked_units')} of "
                    f"{(r.get('booked_units') or 0) + (r.get('unbooked_units') or 0)} units) at {cp}% progress"})
    return out


def score_projects(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    weights = cfg["weights"]
    rows = []
    for r in df.to_dict("records"):
        comps = components(r, cfg)
        s, cov = score(comps, weights)
        rows.append({"project_id": r["project_id"], "risk_score": s, "score_coverage": cov,
                     **{f"risk_{k}": v for k, v in comps.items()}, "risk_flags": flags(r, cfg)})
    return pd.DataFrame(rows)


def score_developers(proj: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Units-weighted mean of project components, then the same weighting into a developer score."""
    comp_cols = [c for c in proj.columns if c.startswith("risk_") and c not in ("risk_score", "risk_flags")]
    rows = []
    for dev_id, g in proj[proj["developer_id"].notna()].groupby("developer_id"):
        w = g["total_units"].fillna(0).astype(float)
        if w.sum() <= 0:
            w = pd.Series(1.0, index=g.index)
        comps = {}
        for c in comp_cols:
            mask = g[c].notna()
            comps[c[5:]] = None if not mask.any() else round(float((g.loc[mask, c] * w[mask]).sum() / w[mask].sum()
                                                                   if w[mask].sum() > 0 else g.loc[mask, c].mean()), 1)
        s, cov = score(comps, cfg["weights"])
        flag_counts: dict[str, int] = {}
        for fl in g["risk_flags"]:
            for x in fl or []:
                flag_counts[x["flag"]] = flag_counts.get(x["flag"], 0) + 1
        rows.append({"developer_id": int(dev_id), "risk_score": s, "score_coverage": cov,
                     **{f"risk_{k}": v for k, v in comps.items()}, "flag_counts": flag_counts})
    return pd.DataFrame(rows)
