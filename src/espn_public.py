"""Fetch ESPN's public fantasy-basketball player feed (same API the espn-api package uses).

No login needed. Gives per-game logs, season averages, injury status, eligible
positions and ESPN ADP. Raw JSON is cached gzipped in data/raw/.
"""
from __future__ import annotations

import gzip
import json
import time

import requests

from .common import RAW, utcnow

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}/segments/0/leaguedefaults/1"


def _path(season: int):
    return RAW / f"espn_players_{season}.json.gz"


def fetch_players(season: int, limit: int = 900, max_age_hours: float = 12, force: bool = False) -> list[dict]:
    """Return ESPN player records for a season, from cache if fresh enough."""
    RAW.mkdir(parents=True, exist_ok=True)
    path = _path(season)
    if path.exists() and not force:
        age_h = (time.time() - path.stat().st_mtime) / 3600
        if age_h < max_age_hours:
            with gzip.open(path, "rt") as f:
                return json.load(f)["players"]
    flt = {"players": {"limit": limit, "sortPercOwned": {"sortPriority": 1, "sortAsc": False}}}
    try:
        r = requests.get(BASE.format(season=season), params={"view": "kona_player_info"},
                         headers={"X-Fantasy-Filter": json.dumps(flt)}, timeout=120)
        r.raise_for_status()
        data = r.json()
    except Exception as e:  # say so clearly; fall back to stale cache if we have it
        if path.exists():
            print(f"[WARN] ESPN fetch for {season} failed ({e}); using cached file from "
                  f"{time.ctime(path.stat().st_mtime)}")
            with gzip.open(path, "rt") as f:
                return json.load(f)["players"]
        raise RuntimeError(f"ESPN fetch for season {season} failed and no cache exists: {e}") from e
    data["_fetched_utc"] = utcnow().isoformat()
    with gzip.open(path, "wt") as f:
        json.dump(data, f)
    return data["players"]
