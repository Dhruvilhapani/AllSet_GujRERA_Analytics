"""Extension sources.

Extensions are not exposed as a separate list; they are reconstructed (parser/project_parser.py) from:
  1. project applications (applicationType = 'Extension'): application no., submission & approval dates,
     certificate uid;
  2. the registration-number suffix  .../EXn/<approved DDMMYY>/<valid-till DDMMYY>;
  3. list fields projOrgFDate (original completion), endDate / extDate (current);
  4. FORM F extension certificates (downloaded PDFs): 'valid till' date and previous registration number.
"""
from scraper.endpoints import PROJECT_ENDPOINTS

ENDPOINTS = [e for e in PROJECT_ENDPOINTS if e.family == "extensions"]
