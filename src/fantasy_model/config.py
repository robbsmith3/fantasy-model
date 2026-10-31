"""Paths and league config. Override locations with env vars for tests/cron."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("FFM_DATA_DIR", PROJECT_ROOT / "data"))
DB_PATH = Path(os.environ.get("FFM_DB_PATH", DATA_DIR / "guillotine.duckdb"))
CONFIG_PATH = Path(os.environ.get("FFM_CONFIG", PROJECT_ROOT / "config" / "leagues.toml"))
# Secrets live outside the repo, chmod 600.
SECRETS_DIR = Path(os.environ.get("FFM_SECRETS_DIR", Path.home() / ".config" / "fantasy-model"))


def load_config(path: Path = CONFIG_PATH) -> dict:
    if not path.exists():
        raise SystemExit(f"Missing {path}. Copy config/leagues.example.toml to it.")
    cfg = tomllib.loads(path.read_text())
    cfg.setdefault("league", [])
    cfg.setdefault("me", {})
    cfg.setdefault("projections", {"weeks_ahead": 4})
    return cfg


def leagues(cfg: dict, platform: str) -> list[dict]:
    return [lg for lg in cfg["league"] if lg.get("platform") == platform and lg.get("league_id")]
