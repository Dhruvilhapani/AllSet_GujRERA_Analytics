from parser.common import (classify_unit_type, ddmmyy, normalize_name, parse_date, parse_month_label, parse_reg_no,
                           salted_hash, to_float)
from parser.complaint_parser import classify_ack


def test_parse_date_formats():
    assert parse_date("24-April-2014") == "2014-04-24"
    assert parse_date("2018-12-31 00:00:00.0") == "2018-12-31"
    assert parse_date("2017-10-06T00:00:00.000+0530") == "2017-10-06"
    assert parse_date("06-10-2017") == "2017-10-06"
    assert parse_date("31-Oct-2023") == "2023-10-31"
    assert parse_date("Date Not Found") is None
    assert parse_date("") is None
    assert parse_date("31-02-2020") is None          # invalid dates are rejected, never coerced


def test_month_label():
    assert parse_month_label("March 2018") == "2018-03-01"
    assert parse_month_label("Oct-17") == "2017-10-01"
    assert parse_month_label("garbage") is None


def test_reg_no_new_registration():
    r = parse_reg_no("PR/GJ/SURAT/CHORYASI/Surat Municipal Corporation/RAA17409/051026/311230")
    assert r.serial == "RAA17409" and r.ex_count == 0
    assert r.issue_date == "2026-10-05" and r.valid_till == "2030-12-31"


def test_reg_no_extension_chain():
    r = parse_reg_no("PR/GJ/VADODARA/VADODARA/Others/MAA00393/EX2/EX3/110325/250126")
    assert r.serial == "MAA00393" and r.ex_count == 3 and r.ex_markers == ["EX2", "EX3"]
    assert r.issue_date == "2025-03-11" and r.valid_till == "2026-01-25"


def test_reg_no_old_extension_and_alteration():
    r = parse_reg_no("PR/GJ/JAMNAGAR/JAMNAGAR/Others/RAA00742/EX1/240220")
    assert r.ex_count == 1 and r.issue_date == "2020-02-24" and r.valid_till is None
    a = parse_reg_no("PR/GJ/AHMEDABAD/AHMADABAD CITY/Others/MAA00001/A1M/031218")
    assert a.alteration_markers == ["A1M"] and a.serial == "MAA00001"
    assert parse_reg_no("Unregistered Project") is None


def test_ddmmyy_invalid():
    assert ddmmyy("320120") is None
    assert ddmmyy("12345") is None


def test_normalize_name_is_conservative():
    assert normalize_name("ABC Developers Pvt Ltd") == normalize_name("A.B.C. Developers Private Limited")
    assert normalize_name("M/s. Shree Ram & Co.") == "shree ram and co"


def test_salted_hash_stable_and_salted():
    assert salted_hash("ABCDE1234F", "s1") == salted_hash("abcde1234f", "s1")
    assert salted_hash("ABCDE1234F", "s1") != salted_hash("ABCDE1234F", "s2")
    assert salted_hash(None, "s1") is None


def test_unit_classification():
    assert classify_unit_type("2 BHK") == "residential"
    assert classify_unit_type("Shops ground floor") == "commercial"
    assert classify_unit_type("SHOPS, FLATS, DUPLEX") == "mixed_unspecified"
    assert classify_unit_type("53") == "other"


def test_classify_ack():
    assert classify_ack("SMC/NCAR/ONLINE/Vadodara/17022026/00170") == ("action", "suo_motu_proceeding", "NCAR")
    assert classify_ack("GJ/CMP/NCQR/Ahmedabad/220209/0002") == ("action", "suo_motu_proceeding", "NCQR")
    assert classify_ack("NCQR-15/2018")[2] == "NCQR"
    assert classify_ack("CMP/A/ONLINE/Valsad/06112025/00585")[1] == "rera_complaint"
    assert classify_ack("CMP/B/OFFLINE/Surat/01012024/00001")[1] == "adjudication_complaint"
    assert classify_ack("A/881/2026")[1] == "rera_complaint"
    assert classify_ack("something else")[1] == "unknown"


def test_to_float():
    assert to_float("1,234.5") == 1234.5
    assert to_float("8.07596E7") == 80759600.0
    assert to_float("NA") is None
