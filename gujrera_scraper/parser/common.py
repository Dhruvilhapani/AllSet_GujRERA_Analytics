"""Shared parsing helpers: dates, numbers, registration numbers, names, hashing."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
     "November", "December"], start=1)}
MONTHS.update({k[:3]: v for k, v in list(MONTHS.items())})

_NULLISH = {"", "-", "na", "n/a", "null", "none", "date not found", "date not exist", "not applicable"}


def clean_str(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in _NULLISH else s


def to_float(v) -> float | None:
    s = clean_str(v)
    if s is None:
        return None
    try:
        return float(s.replace(",", ""))
    except ValueError:
        return None


def to_int(v) -> int | None:
    f = to_float(v)
    return None if f is None else int(round(f))


_DATE_PATTERNS = [
    # ISO-ish with time / offset: 2017-10-06T00:00:00.000+0530, 2018-12-31 00:00:00.0
    (re.compile(r"^(\d{4})-(\d{2})-(\d{2})"), "ymd"),
    # 06-10-2017, 06/10/2017, 06.10.2017
    (re.compile(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})"), "dmy"),
    # 24-April-2014, 31-Oct-2023, 31 Oct 2023
    (re.compile(r"^(\d{1,2})[-\s]([A-Za-z]{3,9})[-\s,]+(\d{4})"), "dMy"),
]


def parse_date(v) -> str | None:
    """Return ISO date (YYYY-MM-DD) or None. Never guesses ambiguous inputs."""
    s = clean_str(v)
    if s is None:
        return None
    if isinstance(v, (int, float)) and v > 10_000_000_000:  # epoch millis
        return datetime.fromtimestamp(v / 1000, tz=timezone(timedelta(hours=5, minutes=30))).date().isoformat()
    for rx, kind in _DATE_PATTERNS:
        m = rx.match(s)
        if not m:
            continue
        try:
            if kind == "ymd":
                d = date(int(m[1]), int(m[2]), int(m[3]))
            elif kind == "dmy":
                d = date(int(m[3]), int(m[2]), int(m[1]))
            else:
                mon = MONTHS.get(m[2].lower()) or MONTHS.get(m[2][:3].lower())
                if not mon:
                    return None
                d = date(int(m[3]), mon, int(m[1]))
        except ValueError:
            return None
        return d.isoformat()
    return None


def parse_month_label(v) -> str | None:
    """'March 2018' -> '2018-03-01'; 'Oct-17' -> '2017-10-01'."""
    s = clean_str(v)
    if not s:
        return None
    m = re.match(r"^([A-Za-z]{3,9})[\s-]+(\d{2,4})$", s)
    if not m:
        return None
    mon = MONTHS.get(m[1].lower()) or MONTHS.get(m[1][:3].lower())
    if not mon:
        return None
    y = int(m[2])
    if y < 100:
        y += 2000
    return date(y, mon, 1).isoformat()


def ddmmyy(token: str) -> str | None:
    """'250126' -> '2026-01-25' (token format used inside GujRERA registration numbers)."""
    if not re.fullmatch(r"\d{6}", token or ""):
        return None
    try:
        return date(2000 + int(token[4:6]), int(token[2:4]), int(token[0:2])).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------- registration numbers
_SERIAL_RX = re.compile(r"/([A-Z]{3}\d{4,6})(?=/|$)")


@dataclass
class RegNo:
    raw: str
    serial: str | None
    ex_markers: list[str] = field(default_factory=list)
    ex_count: int = 0
    alteration_markers: list[str] = field(default_factory=list)   # e.g. A1M (project alteration approvals)
    date_tokens: list[str] = field(default_factory=list)
    issue_date: str | None = None       # first DDMMYY token after the serial/EX markers
    valid_till: str | None = None       # second DDMMYY token, when present (newer format)
    base: str | None = None             # everything up to and including the serial


def parse_reg_no(s) -> RegNo | None:
    """Decode a GujRERA registration / extension number.

    Observed formats (all verified against extension certificates):
      PR/GJ/AHMEDABAD/DASKROI/Others/RAA00362/061017                  issue 06-10-2017
      PR/GJ/JAMNAGAR/JAMNAGAR/Others/RAA00742/EX1/240220              EX1 approved 24-02-2020
      PR/GJ/VADODARA/VADODARA/Others/MAA00393/EX2/190924/250125       EX2 approved 19-09-2024, valid till 25-01-2025
      PR/GJ/VADODARA/VADODARA/Others/MAA00393/EX2/EX3/110325/250126   EX3 (chain), valid till 25-01-2026
      PR/GJ/SURAT/CHORYASI/Surat Municipal Corporation/RAA17409/051026/311230   new registration, valid till 31-12-2030
    """
    s = clean_str(s)
    if not s or not s.upper().startswith("PR/"):
        return None
    m = _SERIAL_RX.search(s)
    if not m:
        return RegNo(raw=s, serial=None)
    serial = m.group(1)
    tail = [t for t in s[m.end():].split("/") if t]
    ex = [t for t in tail if re.fullmatch(r"EX\d+", t)]
    dates = [t for t in tail if re.fullmatch(r"\d{6}", t)]
    ex_count = max((int(t[2:]) for t in ex), default=0)
    alt = [t for t in tail if re.fullmatch(r"A\d+M?", t)]
    return RegNo(raw=s, serial=serial, ex_markers=ex, ex_count=ex_count, alteration_markers=alt, date_tokens=dates,
                 issue_date=ddmmyy(dates[0]) if dates else None,
                 valid_till=ddmmyy(dates[1]) if len(dates) > 1 else None,
                 base=s[:m.end()])


def reg_serial(s) -> str | None:
    r = parse_reg_no(s)
    return r.serial if r else None


# ---------------------------------------------------------------------- names
_LEGAL_SUFFIXES = [
    (r"\bprivate\b", "pvt"), (r"\bpvt\.?", "pvt"), (r"\(p\)", "pvt"), (r"\blimited\b", "ltd"),
    (r"\bltd\.?", "ltd"), (r"\bl\.l\.p\.?", "llp"), (r"\bcorpn\.?", "corporation"), (r"&", " and "),
    (r"\bm/s\.?", ""), (r"\bmessrs\.?", ""),
]


def normalize_name(name) -> str | None:
    """Normalisation for *matching suggestions only* — never used to merge entities automatically."""
    s = clean_str(name)
    if not s:
        return None
    s = s.lower()
    for pat, rep in _LEGAL_SUFFIXES:
        s = re.sub(pat, rep, s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b([a-z]) (?=[a-z]\b)", r"\1", s)  # "a b c developers" -> "abc developers"
    return re.sub(r"\s+", " ", s).strip() or None


def salted_hash(value, salt: str) -> str | None:
    s = clean_str(value)
    if not s:
        return None
    return hashlib.sha256((salt + "|" + s.upper().replace(" ", "")).encode()).hexdigest()[:32]


def classify_unit_type(raw) -> str:
    s = (clean_str(raw) or "").lower()
    if not s:
        return "other"
    com = re.search(r"shop|office|commercial|showroom|retail|restaurant|hotel|godown|warehouse|industrial", s)
    res = re.search(r"flat|bhk|resid|apartment|appartment|bungalow|villa|house|duplex|tenement|\brow\b|penthouse"
                    r"|dwelling|1rk|\brk\b|studio|twin", s)
    if com and res:
        return "mixed_unspecified"   # one inventory line covering both (e.g. 'SHOPS, FLATS, DUPLEX')
    if com:
        return "commercial"
    if res:
        return "residential"
    if re.search(r"\bplots?\b", s):
        return "plot"
    return "other"


# ---------------------------------------------------------------------- unit-level (Form 3) helpers
# A bare hyphen is NOT a range marker ('A-1-01' is wing-floor-unit); only 'to', '~' and a spaced dash are.
_RANGE_RX = re.compile(r"\bto\b|~|\d\s+[-–]\s+\d", re.I)
_LIST_RX = re.compile(r"[,;&]|\band\b", re.I)


def normalize_unit_label(raw) -> str:
    """Identity form of a unit label: trimmed, whitespace collapsed, case-folded. Never drops characters, so two
    different promoter labels never collide. A blank label becomes '(blank)'."""
    s = re.sub(r"\s+", " ", str(raw)).strip().casefold() if raw is not None else ""
    return s or "(blank)"


def unit_label_kind(raw) -> str:
    """single | range | list. Promoters sometimes file one inventory line for many units ('101 to 904',
    'GF 1 to 14 and FF 1 to 14'); such rows are kept verbatim and flagged, never expanded into invented units."""
    s = str(raw or "").strip()
    if _RANGE_RX.search(s):
        return "range"
    if _LIST_RX.search(s):
        return "list"
    return "single"


# Only statuses observed on the portal are mapped. Anything else is 'other' (kept in status_source), a missing
# status is 'unknown'. SOLD / REGISTERED / AVAILABLE are deliberately not treated as aliases.
UNIT_STATUS_MAP = {"BOOKED": "booked", "UNBOOKED": "unbooked"}


def normalize_unit_status(raw) -> str:
    s = (str(raw).strip().upper() if raw is not None else "")
    if not s:
        return "unknown"
    return UNIT_STATUS_MAP.get(s, "other")


def classify_usage(raw, project_category: str | None = None) -> str:
    """residential | shop | office | plot | other. In Plotted Development projects every row is a plot."""
    if project_category == "plotted":
        return "plot"
    s = (str(raw).strip().lower() if raw is not None else "")
    if re.search(r"resid|flat|bungalow|apartment|house|villa", s):
        return "residential"
    if re.search(r"shop|showroom|retail", s):
        return "shop"
    if "office" in s:
        return "office"
    if re.search(r"\bplots?\b", s):
        return "plot"
    return "other"
