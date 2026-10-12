"""Loads config/settings.yaml (paths resolved relative to the package root)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def settings() -> dict[str, Any]:
    with open(ROOT / "config" / "settings.yaml", encoding="utf-8-sig") as f:
        s = yaml.safe_load(f)
    for k in ("database_path", "documents_dir", "document_text_dir", "exports_dir"):
        s[k] = str((ROOT / s[k]).resolve())
    s["privacy"]["hash_salt"] = os.environ.get("GUJRERA_HASH_SALT", s["privacy"]["hash_salt"])
    return s


def risk_config(path: str | Path | None = None) -> dict[str, Any]:
    with open(path or ROOT / "config" / "risk_scoring.yaml", encoding="utf-8-sig") as f:
        return yaml.safe_load(f)
