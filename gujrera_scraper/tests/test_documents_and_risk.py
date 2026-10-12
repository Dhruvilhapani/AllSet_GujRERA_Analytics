import sqlite3
from pathlib import Path

from database import repositories as repo
from database.db import init_schema
from parser.document_parser import parse_certificate
from analytics.risk_analysis import components, flags, score
from scraper.network_client import OP_LEGACY_SERVER_CONNECT, RateLimiter, legacy_tls_context
from settings import risk_config

FIX = Path(__file__).resolve().parent / "fixtures"

def _txt(name):
    return (FIX / "docs" / name).read_text(encoding="utf-8")


def test_form_c_certificate():
    f = parse_certificate(_txt("form_c_62.txt"))
    assert f["certificate_form"].startswith("FORM C")
    assert f["reg_no"] == "PR/GJ/AHMEDABAD/DASKROI/Others/RAA00362/061017"
    assert f["valid_from"] == "2014-04-24" and f["valid_till"] == "2018-12-31"   # date split across a line break
    assert f["issue_date"] == "2017-10-06"
    assert f["tp_revenue_village"].startswith("76/B")
    assert f["fp_survey_no"].startswith("266")


def test_form_f_certificate():
    f = parse_certificate(_txt("form_f_476.txt"))
    assert f["certificate_form"].startswith("FORM F")
    assert f["reg_no"] == "PR/GJ/VADODARA/VADODARA/Others/MAA00393/EX2/EX3/110325/250126"
    assert f["valid_till"] == "2026-01-25"
    assert f["previous_reg_no"] == "PR/GJ/VADODARA/VADODARA/Others/MAA00393/EX2/190924/250125"
    assert f["issue_date"] == "2025-03-11"


def test_reissued_certificate_keeps_spaces_in_reg_no():
    f = parse_certificate(_txt("form_c_reissued_209.txt"))
    assert f["reg_no"] == "PR/GJ/AHMEDABAD/AHMADABAD CITY/AUDA/MAA00248/EX2/041219"


def test_risk_components_and_renormalisation():
    cfg = risk_config()
    row = {"status_derived": "overdue", "detail_crawled": 1, "days_beyond_current": 365, "extension_count": 2,
           "complaints_total": 2, "complaints_pending": 1, "suo_motu_total": 1, "suo_motu_penalty_orders": 0,
           "complaint_penalty_orders": 0, "challans_total": 1, "timeline_elapsed_pct": 100.0, "completion_pct": 60.0,
           "stalled_flag": 1, "months_since_last_qpr": 12, "unsold_pct": 40.0}
    c = components(row, cfg)
    assert c["delay"] == 50.0                 # 365 / 730
    assert c["extensions"] == 68.0            # 2 x 34
    assert c["complaints"] == 50.0            # 1 resolved/unknown x 20 + 1 pending x 30
    assert c["regulatory"] == 50.0            # 1 suo-motu x 20 + 1 challan x 30
    assert c["completion"] == 80.0            # (100 - 60) / 50
    assert c["stagnation"] == 100.0
    s, cov = score(c, cfg["weights"])
    assert 0 < s <= 100 and cov == 1.0
    # missing components are excluded and weights re-normalised
    s2, cov2 = score({**c, "complaints": None, "regulatory": None}, cfg["weights"])
    assert cov2 == 0.6 and s2 is not None
    names = {f["flag"] for f in flags({**row, "current_completion_date": "2025-10-07"}, cfg)}
    assert {"severe_delay", "repeated_extension", "regulatory_action", "stalled_progress"} <= names


def test_completed_project_has_no_stagnation_or_schedule_gap():
    c = components({"status_derived": "completed", "detail_crawled": 1, "days_beyond_current": 0,
                    "extension_count": 0, "complaints_total": 0, "complaints_pending": 0}, risk_config())
    assert c["stagnation"] == 0 and c["completion"] == 0 and c["delay"] == 0


def test_upsert_project_tracks_changes():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    repo.upsert_project(conn, {"project_id": 1, "status_derived": "ongoing", "latest_progress_pct": 40.0}, "r1", "t1")
    changes = repo.upsert_project(conn, {"project_id": 1, "status_derived": "overdue", "latest_progress_pct": 40.0},
                                  "r2", "t2")
    assert changes == [("status_derived", "ongoing", "overdue")]
    row = conn.execute("SELECT first_seen_at, last_changed_at FROM projects").fetchone()
    assert row["first_seen_at"] == "t1" and row["last_changed_at"] == "t2"
    hist = conn.execute("SELECT field, old_value, new_value, run_id FROM project_status_history").fetchall()
    assert [tuple(h) for h in hist] == [("status_derived", "ongoing", "overdue", "r2")]


def test_legacy_tls_context_keeps_verification():
    ctx = legacy_tls_context()
    assert ctx.options & OP_LEGACY_SERVER_CONNECT
    import ssl
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname


def test_rate_limiter_spacing():
    import time
    rl = RateLimiter(20)
    t0 = time.monotonic()
    for _ in range(5):
        rl.wait()
    assert time.monotonic() - t0 >= 0.18
