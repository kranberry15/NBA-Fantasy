"""Private-league access (needs espn_s2 + SWID cookies from .env).

Used for live draft sync now; in-season features will use the espn-api package's League object.
Cookies are sent only to ESPN and never printed.
"""
from __future__ import annotations

import time

import requests

from .common import norm_name

URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/fba/seasons/{season}/segments/0/leagues/{lid}"


def _cookies(env: dict) -> dict:
    if not env.get("ESPN_S2"):
        raise RuntimeError("ESPN_S2 missing in .env (see config/.env.example)")
    c = {"espn_s2": env["ESPN_S2"]}
    if env.get("ESPN_SWID"):  # optional: espn_s2 alone is enough for reading a league
        c["SWID"] = env["ESPN_SWID"]
    return c


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
    ds = data.get("settings", {}).get("draftSettings", {}) or {}
    order = ds.get("pickOrder") or []
    # ESPN randomizes the order 1 hour before the draft; before that pickOrder is a placeholder
    draft_ms = ds.get("date") or 0
    order_final = bool(data.get("draftDetail", {}).get("inProgress") or data.get("draftDetail", {}).get("drafted")
                       or time.time() * 1000 >= draft_ms - 3600 * 1000 + 60 * 1000)
    teams = {t["id"]: (t.get("name") or f"{t.get('location', '')} {t.get('nickname', '')}").strip()
             for t in data.get("teams", [])}
    team_names = {i + 1: teams.get(tid, f"team {tid}") for i, tid in enumerate(order)}
    mine = norm_name(cfg["my_team_name"])
    my_slot = next((s for s, n in team_names.items() if norm_name(n) == mine), None)
    picks = sorted((p["overallPickNumber"], p["playerId"]) for p in data.get("draftDetail", {}).get("picks", [])
                   if p.get("playerId", -1) > 0)
    return {"picks": picks, "my_slot": my_slot if order_final else None, "team_names": team_names,
            "order_final": order_final, "draft_time_ms": draft_ms}
