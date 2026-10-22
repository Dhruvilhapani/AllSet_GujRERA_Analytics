"""Facilities / amenities from the latest Form 1 (architect's certificate, part B) and per-block floors / lifts.

GujRERA has no marketing amenity list. What it publishes is, per project:
  * the common facilities and infrastructure the project declares (internal roads, water, sewerage, fire fighting,
    security, community buildings, landscaping, ...), each with YES/NO applicability, % work done and planned /
    actual dates;
  * promoter-named extra items (`oneBOtherDetails`, e.g. "CLUB HOUSE") with the same progress fields.
Both are stored as published; nothing is inferred (a facility that is not listed is "not declared", not "absent").
"""
from __future__ import annotations

import re
from typing import Any

from parser.common import clean_str, parse_date, to_float, to_int

# Form 1B item stem -> (label, category). Labels follow the portal's wording; unknown *YesNo stems found in a
# response are still stored (humanised), so a new portal item is never silently dropped.
COMMON_ITEMS: dict[str, tuple[str, str]] = {
    "internalRoad": ("Internal roads and footpaths", "roads"),
    "streetLighting": ("Street lighting", "roads"),
    "compoundWall": ("Compound wall", "safety_security"),
    "security": ("Security", "safety_security"),
    "plinthProtection": ("Plinth protection", "structure"),
    "landscaping": ("Landscaping and tree planting", "landscaping"),
    "communityBuildings": ("Community buildings", "community"),
    "letterBox": ("Letter boxes", "community"),
    "waterSupply": ("Water supply", "water"),
    "waterTank": ("Water tank", "water"),
    "drinkingWaterFacilities": ("Drinking water facilities", "water"),
    "waterConservation": ("Water conservation / rain-water harvesting", "water"),
    "sewerage": ("Sewerage", "sewage_waste"),
    "stormWaterDrains": ("Storm-water drains", "sewage_waste"),
    "treatmentAndDisposal": ("Treatment and disposal of sewage and sullage", "sewage_waste"),
    "solidWasteManagement": ("Solid waste management and disposal", "sewage_waste"),
    "electricalMeterRoom": ("Electrical meter room / sub-station", "electrical"),
    "useofRenewableEnergy": ("Use of renewable energy", "energy"),
    "energyManagement": ("Energy management", "energy"),
    "fireFighting": ("Fire fighting facilities", "fire_safety"),
    "fireProtection": ("Fire protection and fire safety requirements", "fire_safety"),
    "emergencyEvacuation": ("Emergency evacuation services", "fire_safety"),
    "otherRequirement": ("Other requirements", "other"),
}


def _humanise(stem: str) -> str:
    s = re.sub(r"(?<!^)(?=[A-Z])", " ", stem).lower()
    return s[:1].upper() + s[1:]


_NO_REMARK = re.compile(r"^(no|no remarks?|nil|none|n\.?a\.?|not applicable|not available|[-.\s]*)$", re.I)


def _remark(v: Any) -> str | None:
    """Promoters type placeholders ("NO REMARK", "NIL", "-") where there is no remark: those are not remarks."""
    s = clean_str(v)
    return None if s is None or _NO_REMARK.match(s) else " ".join(s.split())


def _yes_no(v: Any) -> str | None:
    s = (clean_str(v) or "").upper()
    return s if s in ("YES", "NO") else None


def form_one_data(raw: dict) -> dict | None:
    v = raw.get("form_one")
    j = v[0] if v else None
    d = j.get("data") if isinstance(j, dict) else None
    return d if isinstance(d, dict) and isinstance(d.get("formOneB"), dict) else None


def parse_facilities(project_id: int, raw: dict) -> list[dict]:
    """project_amenities rows (PK project_id, item_key). Empty when Form 1 was not fetched / not published."""
    d = form_one_data(raw)
    if d is None:
        return []
    b = d["formOneB"]
    form_one_id = to_int(d.get("formOneId"))
    submitted = parse_date(d.get("submitedOn"))
    rows: list[dict] = []
    stems = list(COMMON_ITEMS) + sorted(k[:-5] for k in b if k.endswith("YesNo") and k[:-5] not in COMMON_ITEMS)
    for stem in stems:
        if f"{stem}YesNo" not in b and f"{stem}WorkDone" not in b:
            continue   # the portal did not return this item for this project: not declared
        label, category = COMMON_ITEMS.get(stem, (_humanise(stem), "other"))
        rows.append({
            "project_id": project_id, "item_key": stem, "label": label, "category": category,
            "source": "form_one_common", "applicable": _yes_no(b.get(f"{stem}YesNo")),
            "progress_pct": to_float(b.get(f"{stem}WorkDone")),
            "planned_start": parse_date(b.get(f"{stem}StartActivityDate")),
            "planned_end": parse_date(b.get(f"{stem}EndActivityDate")),
            "actual_start": parse_date(b.get(f"{stem}StartActualDate")),
            "actual_end": parse_date(b.get(f"{stem}EndActualDate")),
            "remarks": _remark(b.get(f"{stem}Remarks")), "form_one_id": form_one_id, "form1_submitted_on": submitted,
        })
    for o in b.get("oneBOtherDetails") or []:
        if not isinstance(o, dict) or _remark(o.get("caption")) is None:
            continue   # blank or a typed placeholder ("No", "NA", "Nil"): not an amenity
        rows.append({
            "project_id": project_id, "item_key": f"other:{o.get('fromBOntherId') or len(rows)}",
            "label": " ".join(clean_str(o["caption"]).split()).title(), "category": "named_by_promoter",
            "source": "form_one_other", "applicable": "YES", "progress_pct": to_float(o.get("otherWorkDone")),
            "planned_start": parse_date(o.get("activityStartDate")), "planned_end": parse_date(o.get("activityEndDate")),
            "actual_start": parse_date(o.get("actualStartDate")), "actual_end": parse_date(o.get("actualEndDate")),
            "remarks": _remark(o.get("otherRemarks")), "form_one_id": form_one_id, "form1_submitted_on": submitted,
        })
    seen, out = set(), []
    for r in rows:   # one row per item key
        if r["item_key"] not in seen:
            seen.add(r["item_key"])
            out.append(r)
    return out


def parse_block_floors(raw: dict) -> dict[str, dict]:
    """block name -> {floors, lifts} from Form 1 part A (null when the architect did not fill them in)."""
    d = form_one_data(raw)
    out: dict[str, dict] = {}
    for a in (d or {}).get("formOneAList") or []:
        name = clean_str(a.get("blockName")) if isinstance(a, dict) else None
        if name:
            out[name] = {"floors": to_int(a.get("noOfFloors")), "lifts": to_int(a.get("noOfLifts"))}
    return out
