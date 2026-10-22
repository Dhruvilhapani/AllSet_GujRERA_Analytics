"""Unit layer: turns the stored Form 3 / unit-list responses of one project into normalized rows.

Sources (all public, see docs/data_dictionary.md):
  qtr_form_ids   -> id of the latest Form 3
  form_three     -> Form 3 header (submission date, declared totals)
  block_names / block_chart / block_master -> block names
  unit_list      -> one POST response per block: one row per unit

Rules
  * Source values are stored as published (`*_raw`, `status_source`); normalised columns are derived separately.
  * `area_excl_balcony_terrace_sqm` is ONE source field (exclusive balcony/veranda AND open terrace). It is never split.
  * Personal fields (allottee name, KYC type/id, mobile) and the masked amounts are never read into the output.
  * A project is `complete` only if every expected block returned unit rows; otherwise the state says why.
    Missing data is never turned into "unbooked".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from parser.common import (classify_usage, clean_str, normalize_unit_label, normalize_unit_status, parse_date,
                           to_float, to_int, unit_label_kind)
from scraper.units import SOURCES, block_candidates

STATES = ("complete", "partial", "not_published", "error", "not_fetched")


@dataclass
class UnitParse:
    state: str = "not_fetched"
    note: str | None = None
    units: list[dict] = field(default_factory=list)
    form3: dict | None = None
    block_fetch: list[dict] = field(default_factory=list)
    block_stats: dict[str, dict] = field(default_factory=dict)      # block name -> unit_* counts
    block_master: dict[str, dict] = field(default_factory=dict)     # block name -> master fields
    blocks_ok: set[str] = field(default_factory=set)
    form_three_id: int | None = None
    form3_submitted_on: str | None = None
    scraped_at: str | None = None
    issues: list[str] = field(default_factory=list)

    def project_fields(self) -> dict:
        return {"unit_data_state": self.state, "unit_data_note": self.note, "units_scraped_at": self.scraped_at,
                "units_row_count": len(self.units) if self.state in ("complete", "partial") else None,
                "units_form3_submitted_on": self.form3_submitted_on}


def _json(raw: dict, key: str) -> Any:
    v = raw.get(key)
    return v[0] if v and v[0] is not None else None


def _data(j: Any) -> Any:
    return j.get("data") if isinstance(j, dict) else None


def latest_form_three_id(raw: dict) -> int | None:
    d = _data(_json(raw, "qtr_form_ids"))
    return to_int(d.get("formThreeId")) if isinstance(d, dict) else None


def parse_form3_header(project_id: int, form_three_id: int, j: Any, is_latest: bool, now: str) -> dict | None:
    d = _data(j)
    if not isinstance(d, dict) or to_int(d.get("formThreeId")) != form_three_id:
        return None
    return {"project_id": project_id, "form_three_id": form_three_id, "form_status": clean_str(d.get("formThreeStatus")),
            "submitted_on": parse_date(d.get("submissionOn")), "number_of_units": to_int(d.get("numberOfUnits")),
            "booked_declared": to_int(d.get("bookedUnit")), "unbooked_declared": to_int(d.get("unBookedUnit")),
            "total_residential": to_int(d.get("totalResidential")), "total_shop": to_int(d.get("totalShop")),
            "total_office": to_int(d.get("totalOffice")), "total_others": to_int(d.get("totalOthers")),
            "is_latest": int(is_latest), "first_seen_at": now, "last_seen_at": now, "source_endpoint": "form_three"}


def build_unit_rows(block: str, rows: list[dict], form_three_id: int, submitted_on: str | None,
                    project_category: str | None) -> list[dict]:
    """Normalise the rows of one block. Deterministic order -> deterministic dup_seq."""
    built = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        label_raw = r.get("flatNo")
        agr_raw = r.get("dateOfAgrrement", r.get("dateOfAgreement"))   # the portal misspells the key
        status_raw = clean_str(r.get("status"))
        built.append({
            "block_name": block,
            "unit_no_raw": None if label_raw is None else str(label_raw).strip(),
            "unit_no_norm": normalize_unit_label(label_raw),
            "unit_kind": unit_label_kind(label_raw),
            "block_id_src": clean_str(r.get("blockId")),
            "usage_raw": clean_str(r.get("usage")),
            "usage_class": classify_usage(r.get("usage"), project_category),
            "carpet_area_sqm": to_float(r.get("carpetArea")),
            "area_excl_balcony_terrace_sqm": to_float(r.get("areaofExBalcony")),
            "status_source": status_raw,
            "status_norm": normalize_unit_status(status_raw),
            "agreement_date": parse_date(agr_raw),
            "agreement_date_raw": clean_str(agr_raw),
            "encumbrance_status": clean_str(r.get("encumbranceStatus")),
            "redeveloped_flag": clean_str(r.get("redeveloped")),
            "source_row_id": to_int(r.get("id")),
            "form_three_id": form_three_id,
            "form3_submitted_on": submitted_on,
        })
    built.sort(key=lambda u: (u["unit_no_norm"], u["usage_raw"] or "", -1 if u["carpet_area_sqm"] is None else u["carpet_area_sqm"],
                              -1 if u["area_excl_balcony_terrace_sqm"] is None else u["area_excl_balcony_terrace_sqm"],
                              u["status_source"] or "", u["agreement_date_raw"] or ""))
    seq: dict[str, int] = {}
    for u in built:
        n = seq.get(u["unit_no_norm"], 0)
        u["dup_seq"] = n
        seq[u["unit_no_norm"]] = n + 1
    return built


def parse_units(project_id: int, project_category: str | None, raw: dict, unit_raw: dict[str, tuple[Any, dict]],
                failed_blocks: set[str], now: str) -> UnitParse:
    """`raw`: the project's {endpoint_key: (json, meta)}. `unit_raw`: {"<pid>|<block>": (json, meta)} of unit_list.
    `failed_blocks`: block names whose unit_list task failed after all attempts (HTTP errors)."""
    up = UnitParse()
    if "qtr_form_ids" not in raw:
        up.state, up.note = "not_fetched", "latest Form 3 id was not fetched yet"
        return up
    ft = latest_form_three_id(raw)
    if not ft:
        up.state, up.note = "not_published", "no Form 3 is published for this project"
        return up
    up.form_three_id = ft

    header = parse_form3_header(project_id, ft, _json(raw, "form_three"), True, now)
    up.form3 = header
    up.form3_submitted_on = header["submitted_on"] if header else None

    names, expected, src = block_candidates({k: _json(raw, k) for k in SOURCES})
    if not names:
        up.state, up.note = "not_published", "no block names are published, so no unit list can be requested"
        return up

    total_rows = 0
    outcomes: dict[str, str] = {}
    for name in names:
        entry = unit_raw.get(f"{project_id}|{name}")
        rows_json, meta = (entry if entry else (None, None))
        url = (meta or {}).get("url", "")
        row = {"project_id": project_id, "block_name": name, "form_three_id": ft,
               "in_expected": int(name in expected), "fetched_at": (meta or {}).get("fetched_at"),
               "http_status": 200 if meta else None, "row_count": None}
        # a stored response for an older Form 3 id is stale: treat it as not fetched yet
        if entry is None or not url.endswith(f"formThreeId={ft}"):
            row["outcome"] = "error" if name in failed_blocks else "not_fetched"
            row["http_status"] = None
        else:
            data = _data(rows_json)
            if not isinstance(data, list):
                row["outcome"] = "error"
            else:
                units = build_unit_rows(name, data, ft, up.form3_submitted_on, project_category)
                row["row_count"] = len(units)
                row["outcome"] = "ok" if units else "empty"
                if units:
                    up.units.extend(units)
                    up.blocks_ok.add(name)
                    total_rows += len(units)
                    counts = {"unit_rows": len(units),
                              "unit_booked": sum(u["status_norm"] == "booked" for u in units),
                              "unit_unbooked": sum(u["status_norm"] == "unbooked" for u in units)}
                    counts["unit_other"] = len(units) - counts["unit_booked"] - counts["unit_unbooked"]
                    counts["block_id_src"] = next((to_int(u["block_id_src"]) for u in units if u["block_id_src"]), None)
                    up.block_stats[name] = counts
                if meta and meta.get("fetched_at") and (up.scraped_at or "") < meta["fetched_at"]:
                    up.scraped_at = meta["fetched_at"]
        outcomes[name] = row["outcome"]
        up.block_fetch.append(row)

    bad = [n for n, o in outcomes.items() if o in ("error", "not_fetched")]
    empty_expected = [n for n in expected if outcomes.get(n) == "empty"]
    if bad and not up.units:
        up.state = "error" if any(outcomes[n] == "error" for n in bad) else "not_fetched"
        up.note = f"unit lists missing for blocks: {', '.join(bad)}"
    elif bad:
        up.state, up.note = "partial", f"unit lists missing for blocks: {', '.join(bad)}"
    elif not up.units:
        up.state = "not_published"
        up.note = f"every block ({', '.join(names)}) returned an empty unit list"
    elif not empty_expected:
        up.state = "complete"
    elif header and header["number_of_units"] == total_rows:
        up.state = "complete"
        up.note = f"empty blocks ({', '.join(empty_expected)}) ignored: unit rows match the Form 3 total ({total_rows})"
    else:
        up.state = "partial"
        up.note = f"expected blocks without unit rows: {', '.join(empty_expected)} (names from {src})"
    return up


def parse_block_master(raw: dict) -> dict[str, dict]:
    rows = _data(_json(raw, "block_master"))
    out: dict[str, dict] = {}
    for r in rows if isinstance(rows, list) else []:
        name = clean_str(r.get("blockName"))
        if name:
            out[name] = {"block_id_src": to_int(r.get("blockId")), "height": to_float(r.get("height")),
                         "fsi": to_float(r.get("fsi")), "builtup_area_sqm": to_float(r.get("builtupArea")),
                         "dev_start_date": parse_date(r.get("devStartDate")), "dev_end_date": parse_date(r.get("devEndDate"))}
    return out


def merge_into_blocks(project_id: int, block_rows: list[dict], up: UnitParse, master: dict[str, dict],
                      extra: dict[str, dict] | None = None) -> list[dict]:
    """Enrich the ProjectParser's project_blocks rows with block-master data and the unit-row counts; blocks that
    only exist in the unit sources get a row too (so the UI can list them)."""
    by_name = {b["block_name"]: b for b in block_rows}
    extra = extra or {}
    for name in list(up.block_stats) + [n for n in master if n not in up.block_stats]:
        if name not in by_name:
            row = {"project_id": project_id, "block_name": name, "source_endpoint": ""}
            by_name[name] = row
            block_rows.append(row)
    for name, row in by_name.items():
        stats = up.block_stats.get(name)
        if stats:
            row.update(stats)
            row["source_endpoint"] = ",".join(filter(None, [row.get("source_endpoint"), "unit_list"]))
        m = master.get(name)
        if m:
            for k, v in m.items():
                if k in ("dev_start_date", "dev_end_date"):
                    row[k] = row.get(k) or v      # block_progress dates (already parsed) win over the master
                else:
                    row[k] = v if v is not None else row.get(k)
            row["source_endpoint"] = ",".join(filter(None, [row.get("source_endpoint"), "block_master"]))
        if name in extra:   # Form 1 floors / lifts (only attached to blocks that exist in another source)
            row.update({k: v for k, v in extra[name].items() if v is not None})
    return block_rows
