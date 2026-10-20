"""Private-league access (needs espn_s2 + SWID cookies from .env).

Used for live draft sync now; in-season features will use the espn-api package's League object.
Cookies are sent only to ESPN and never printed.
"""
from __future__ import annotations

import requests

from .common import norm_name

URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}/segments/0/leagues/{lid}"


def _cookies(env: dict) -> dict:
    if not env.get("ESPN_S2") or not env.get("ESPN_SWID"):
        raise RuntimeError("ESPN_S2 / ESPN_SWID missing in .env (see config/.env.example)")
    return {"espn_s2": env["ESPN_S2"], "SWID": env["ESPN_SWID"]}


def fetch_draft(cfg: dict, env: dict, timeout: float = 5) -> dict:
    """Return {'picks': [(overall_pick, player_id)], 'my_slot': int|None, 'team_names': {slot: name}}."""
    lid = cfg.get("league_id")
    if not lid:
        raise RuntimeError(f"league_id not set in config/{cfg['key']}.yaml")
    r = requests.get(URL.format(season=cfg["season"], lid=lid),
                     params=[("view", "mDraftDetail"), ("view", "mSettings"), ("view", "mTeam")],
                     cookies=_cookies(env), timeout=timeout)
    if r.status_code in (401, 403):
        raise RuntimeError(f"ESPN refused access (HTTP {r.status_code}). Cookies expired or wrong league_id.")
    r.raise_for_status()
    data = r.json()
    order = (data.get("settings", {}).get("draftSettings", {}) or {}).get("pickOrder") or []
    teams = {t["id"]: (t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}").strip()
             for t in data.get("teams", [])}
    team_names = {i + 1: teams.get(tid, f"team {tid}") for i, tid in enumerate(order)}
    mine = norm_name(cfg["my_team_name"])
    my_slot = next((s for s, n in team_names.items() if norm_name(n) == mine), None)
    picks = sorted((p["overallPickNumber"], p["playerId"]) for p in data.get("draftDetail", {}).get("picks", [])
                   if p.get("playerId", -1) > 0)
    return {"picks": picks, "my_slot": my_slot, "team_names": team_names}
