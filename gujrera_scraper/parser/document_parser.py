"""PDF text extraction and structured-field parsing for GujRERA certificates.

* Registration certificate (FORM C, Rule 6(1)):   reg no, validity start/end, TP / FP / survey / blocks, issue date
* Extension certificate (FORM F, Rule 7(2)):      extension no, valid-till date, previous reg no, issue date
Orders / Form 4 uploads are usually scanned images: text is extracted when present, otherwise the
document is flagged `ocr_needed` (OCR runs only if Tesseract is installed).
"""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from parser.common import clean_str, parse_date

MIN_CHARS_PER_PAGE = 200


def extract_text(pdf_bytes: bytes, ocr_command: str | None = "tesseract") -> tuple[str, str]:
    """Return (text, status) where status in {extracted, ocr, ocr_needed, failed}."""
    try:
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001 - malformed PDFs are expected occasionally
        return "", "failed"
    try:
        text = "\n".join(page.get_text() for page in doc)
        pages = max(doc.page_count, 1)
        if len(text.strip()) >= MIN_CHARS_PER_PAGE * min(pages, 2) * 0.5:
            return text, "extracted"
        exe = shutil.which(ocr_command) if ocr_command else None
        if not exe:
            return text, "ocr_needed"
        out = []
        with tempfile.TemporaryDirectory() as td:
            for i, page in enumerate(doc):
                img = Path(td) / f"p{i}.png"
                page.get_pixmap(dpi=200).save(img)
                r = subprocess.run([exe, str(img), "stdout"], capture_output=True, text=True, timeout=180)
                out.append(r.stdout)
        return "\n".join(out), "ocr"
    finally:
        doc.close()


def _flat(text: str) -> str:
    t = re.sub(r"-\s*\n\s*", "-", text)       # dates broken across lines: "2018-\n12-31"
    return re.sub(r"\s+", " ", t)


def parse_certificate(text: str) -> dict:
    """Parse FORM C / FORM F certificate text into structured fields (missing fields are omitted)."""
    t = _flat(text)
    out: dict = {}
    q = r"[\s\-‘’'\"“”]*"   # 'FORM - C', "FORM ‘F’" ...
    if re.search(rf"FORM{q}C\b{q}.*?REGISTRATION CERTIFICATE", t, re.I):
        out["certificate_form"] = "FORM C (registration)"
    elif re.search(rf"FORM{q}F\b{q}.*?EXTENSION OF REGISTRATION", t, re.I):
        out["certificate_form"] = "FORM F (extension)"
    # registration numbers may contain spaces ("AHMADABAD CITY"), so read the number from its own line
    m = re.search(r"(?:project registration number|project extension number)\s*:?-?\s*\n?\s*(PR/[^\n]+)", text, re.I)
    if m:
        out["reg_no"] = re.sub(r"\s{2,}.*$", "", m.group(1)).strip()
    m = re.search(r"valid\s+for a period of (.+?) commencing from Dt\.?\s*([\d\-/]+) and ending with Dt\.?\s*([\d\-/]+)",
                  t, re.I)
    if m:
        out["validity_period_text"] = m.group(1).strip()
        out["valid_from"] = parse_date(m.group(2))
        out["valid_till"] = parse_date(m.group(3))
    m = re.search(r"valid till Dt\.?\s*([\d\-/]+)", t, re.I)
    if m and "valid_till" not in out:
        out["valid_till"] = parse_date(m.group(1))
    m = re.search(r"previous registration number (PR/.+?) stands", t, re.I)
    if m:
        out["previous_reg_no"] = m.group(1).strip()
    m = re.search(r"Date:\s*([\d\-/]{8,10})\s*Place", t)
    if m:
        out["issue_date"] = parse_date(m.group(1))
    rem = re.search(r"Remarks:\s*(.+?)\s*Promoter Name", t, re.I)
    if rem:
        r = rem.group(1)
        for key, rx in (("tp_revenue_village", r"TP/Revenue Village:\s*([^,]+(?:\([^)]*\))?)"),
                        ("fp_survey_no", r"FP/Survey No:\s*(.+?)(?:, Sub\s*Plot|$)"),
                        ("sub_plot", r"Sub\s*Plot:\s*([^,]*)"),
                        ("blocks", r"Block:\s*(.+)$")):
            mm = re.search(rx, r, re.I)
            if mm and clean_str(mm.group(1)):
                out[key] = clean_str(mm.group(1))
    pm = re.search(r"Promoter Name & Address :-\s*(.+?)\s+Date:", t, re.I)
    if pm:
        out["promoter_block"] = pm.group(1)[:300]
    return out
