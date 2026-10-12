"""QPR / construction-progress endpoints (crawled through the queue; parsed by parser/qpr_parser.py).

  qpr_progress    physical progress % per reported quarter (history)
  qpr_quarters    filing calendar per quarter: due date, submission date, status, defaulter code
  qe_filing       Q-E (project end) filing: Form 4 (architect completion), Form 5, society registration
  qpr_details     end date at registration vs as per extension, block-wise planned inventory
  progress_report portal's own progress label and architect / engineer scores
  block_chart     booked / unbooked units per block (fed by QPR inventory updates)
  unit_summary    total units, carpet area and price range
  block_progress  per-block progress from the latest architect certificate (Form 1)
  form_two        latest engineer certificate (Form 2): per-block work done %, estimated vs incurred cost
"""
from scraper.endpoints import PROJECT_ENDPOINTS

ENDPOINTS = [e for e in PROJECT_ENDPOINTS if e.family == "qpr"]
