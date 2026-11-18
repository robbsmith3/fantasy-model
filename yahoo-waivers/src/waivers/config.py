"""Settings from config.toml and credentials from the environment or a gitignored .env."""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

GAME = "nfl"  # Yahoo resolves "nfl" to the current season


@dataclass(frozen=True)
class League:
    name: str
    league_id: str
    team_id: str

    @property
    def league_key(self) -> str:
        return f"{GAME}.l.{self.league_id}"

    @property
    def team_key(self) -> str:
        return f"{self.league_key}.t.{self.team_id}"


@dataclass(frozen=True)
class Settings:
    leagues: list[League]
    players_per_position: int = 25
    requests_per_second: float = 1.0
    max_retries: int = 5
    faab_budget: int = 100
    env_path: Path | None = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Credentials:
    client_id: str
    client_secret: str
    refresh_token: str | None


class ConfigError(Exception):
    pass


def load_settings(path: Path) -> Settings:
    if not path.exists():
        raise ConfigError(f"{path} not found: copy config.example.toml to {path.name}")
    raw = tomllib.loads(path.read_text())
    leagues = []
    for lg in raw.get("league", []):
        lid, tid = str(lg.get("league_id", "")).strip(), str(lg.get("team_id", "")).strip()
        if not (lid.isdigit() and tid.isdigit()):
            raise ConfigError("each [[league]] needs numeric league_id and team_id")
        leagues.append(League(str(lg.get("name") or lid), lid, tid))
    if not leagues:
        raise ConfigError("no [[league]] configured")
    s = raw.get("settings", {})
    return Settings(
        leagues=leagues,
        players_per_position=int(s.get("players_per_position", 25)),
        requests_per_second=float(s.get("requests_per_second", 1.0)),
        max_retries=int(s.get("max_retries", 5)),
        faab_budget=int(s.get("faab_budget", 100)),
    )


def load_credentials(env_path: Path | None) -> Credentials:
    """Environment first, then .env. Warns if .env is readable by anyone but the owner."""
    if env_path and env_path.exists():
        mode = env_path.stat().st_mode
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            print(f"warning: {env_path} is readable by others; run: chmod 600 {env_path}",
                  file=sys.stderr)  # fmt: skip
        load_dotenv(env_path, override=False)
    cid, secret = os.environ.get("YAHOO_CLIENT_ID"), os.environ.get("YAHOO_CLIENT_SECRET")
    if not cid or not secret:
        raise ConfigError("set YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET (see .env.example)")
    return Credentials(cid, secret, os.environ.get("YAHOO_REFRESH_TOKEN") or None)


def save_refresh_token(env_path: Path, token: str) -> None:
    """Write or replace YAHOO_REFRESH_TOKEN in .env, owner-only (a credential, not Yahoo data)."""
    lines = env_path.read_text().splitlines() if env_path.exists() else []
    lines = [ln for ln in lines if not ln.startswith("YAHOO_REFRESH_TOKEN=")]
    lines.append(f"YAHOO_REFRESH_TOKEN={token}")
    env_path.touch(mode=0o600, exist_ok=True)
    env_path.chmod(0o600)
    env_path.write_text("\n".join(lines) + "\n")
    os.environ["YAHOO_REFRESH_TOKEN"] = token
