"""Promoter master endpoint. The promoterId of every project comes from the project core record; each
unique promoter is fetched once per run (queue de-duplicates on (run, endpoint, promoterId))."""
from scraper.endpoints import PROMOTER_ENDPOINT

ENDPOINTS = [PROMOTER_ENDPOINT]
