"""Block discovery for the unit layer (pure functions over already-fetched JSON).

The unit list of a project is fetched one block at a time (POST `formthree/public/get-inv-details-for-view` with
`{blockName, formThreeId}`), so the crawler first needs the block names. The portal itself takes them from
`citizen_claim/get-project-all-block{id}`, which is empty for some projects; the booking chart
(`block_chart`) and the block master (`block_master`) are used as fallbacks, and every name from every source is
queried because the sources spell combined blocks differently (`A+B` vs `A`, `B`).
"""
from __future__ import annotations

from typing import Any

# Order of trust when deciding which block names a project is *expected* to have (used for completeness).
SOURCES = ("block_names", "block_chart", "block_master")


def _clean(name: Any) -> str | None:
    if name is None:
        return None
    s = str(name).strip()
    return s or None


def names_from_block_names(j: Any) -> list[str]:
    """citizen_claim/get-project-all-block: a plain object {blockName: [unit numbers...]}."""
    if not isinstance(j, dict):
        return []
    if "data" in j and isinstance(j["data"], dict):
        j = j["data"]
    return [n for n in (_clean(k) for k in j) if n and n not in ("status", "message")]


def names_from_block_chart(j: Any) -> list[str]:
    rows = (j or {}).get("data") if isinstance(j, dict) else None
    return [n for n in (_clean(r.get("bname")) for r in rows or [] if isinstance(r, dict)) if n]


def names_from_block_master(j: Any) -> list[str]:
    rows = (j or {}).get("data") if isinstance(j, dict) else None
    return [n for n in (_clean(r.get("blockName")) for r in rows or [] if isinstance(r, dict)) if n]


_EXTRACT = {"block_names": names_from_block_names, "block_chart": names_from_block_chart,
            "block_master": names_from_block_master}


def block_candidates(responses: dict[str, Any]) -> tuple[list[str], list[str], str | None]:
    """`responses` maps source key -> parsed JSON (or None). Returns (names to query, expected names, source of the
    expected names). `expected` comes from the most trusted non-empty source; `names` is the de-duplicated union."""
    per_source = {k: _EXTRACT[k](responses.get(k)) for k in SOURCES}
    names: list[str] = []
    for k in SOURCES:
        for n in per_source[k]:
            if n not in names:
                names.append(n)
    for k in SOURCES:
        if per_source[k]:
            return names, list(dict.fromkeys(per_source[k])), k
    return names, [], None
