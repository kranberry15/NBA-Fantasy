"""Shared paths, config loading, and name helpers."""
from __future__ import annotations

import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
PROCESSED = DATA / "processed"
CONFIG = ROOT / "config"
REPORTS = ROOT / "reports"

# ESPN slot ids -> names (subset we care about)
SLOT_POS = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C"}
# ESPN defaultPositionId (pro position) -> name
DEFAULT_POS = {1: "PG", 2: "SG", 3: "SF", 4: "PF", 5: "C", 6: "G", 7: "F", 8: "F", 9: "F"}
PRO_TEAM = {0: "FA", 1: "ATL", 2: "BOS", 3: "NOP", 4: "CHI", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET",
            9: "GSW", 10: "HOU", 11: "IND", 12: "LAC", 13: "LAL", 14: "MIA", 15: "MIL", 16: "MIN",
            17: "BKN", 18: "NYK", 19: "ORL", 20: "PHI", 21: "PHX", 22: "POR", 23: "SAC", 24: "SAS",
            25: "OKC", 26: "UTA", 27: "WAS", 28: "TOR", 29: "MEM", 30: "CHA"}


def load_league(key: str) -> dict:
    """Load config/<key>.yaml. key is 'league_a' or 'league_b'."""
    path = CONFIG / f"{key}.yaml"
    with open(path) as f:
        cfg = yaml.safe_load(f)
    if cfg.get("key") != key:
        raise ValueError(f"{path} has key={cfg.get('key')!r}, expected {key!r}. Refusing to mix leagues.")
    return cfg


def load_env() -> dict:
    """Read ESPN cookies from .env. Values are never printed."""
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("ESPN_S2", "ESPN_SWID"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def norm_name(name: str) -> str:
    """Lowercase ascii name without punctuation or suffixes, for matching."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z ]", "", s.lower().replace("-", " "))
    s = re.sub(r"\b(jr|sr|ii|iii|iv|v)\b", "", s)
    return " ".join(s.split())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def header(cfg: dict) -> str:
    return f"=== {cfg['league_name']} ({cfg['format'].upper()}) | team: {cfg['my_team_name']} ==="
