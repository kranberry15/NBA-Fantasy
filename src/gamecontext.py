"""Measured explanations for a player's game. No guessing: every tag comes from data.

Sources:
  * ESPN game logs (already cached): minutes, personal fouls, fouled out (DQ or 6 PF), ejection,
    technicals, flagrants, started (GS), shooting.
  * ESPN public scoreboard (cached per day): final margin (blowout), overtime, back-to-back.
  * Teammate absences: rotation players (projected 20+ MPG) on the same team with no log that game.
  * ESPN fantasy news (Rotowire feed, cached daily): role / injury notes, shown verbatim.
"""
from __future__ import annotations

import datetime as dt
import json
import time
from datetime import date

import numpy as np
import pandas as pd
import requests

from .common import PRO_TEAM, RAW

DAY1 = dt.date(2026, 10, 20)   # scoring period 1
ABBR = {"NY": "NYK", "SA": "SAS", "GS": "GSW", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}
BLOWOUT = 20
FOUL_TROUBLE = 5               # 5+ fouls limits minutes; 6 = fouled out


def period_date(sp: int) -> dt.date:
    return DAY1 + dt.timedelta(days=int(sp) - 1)


def scoreboard(periods) -> dict[tuple[str, int], dict]:
    """(team, period) -> {margin, ot, opp, home}. Completed days are cached forever."""
    cache = RAW / "scoreboard_context.json"
    have = json.loads(cache.read_text()) if cache.exists() else {}
    for sp in sorted(set(int(p) for p in periods)):
        day = period_date(sp)
        if str(sp) in have or day >= date.today():
            continue
        try:
            r = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                             params={"dates": day.strftime("%Y%m%d")}, timeout=20).json()
        except Exception as e:
            print(f"[WARN] ESPN scoreboard {day} failed: {e}")
            continue
        games = {}
        for ev in r.get("events", []):
            if not ev["status"]["type"].get("completed"):
                continue
            c = ev["competitions"][0]["competitors"]
            ab = [ABBR.get(x["team"]["abbreviation"], x["team"]["abbreviation"]) for x in c]
            sc = [int(x["score"]) for x in c]
            for i in (0, 1):
                games[ab[i]] = {"margin": sc[i] - sc[1 - i], "ot": ev["status"].get("period", 4) > 4,
                                "opp": ab[1 - i], "home": c[i].get("homeAway") == "home"}
        have[str(sp)] = games
    cache.write_text(json.dumps(have))
    return {(t, int(sp)): g for sp, games in have.items() for t, g in games.items()}


def news(player_id: int, since: str = "2026-09-15", max_age_h: float = 12) -> list[dict]:
    """ESPN fantasy news (Rotowire) for a player, newest first. Cached per player."""
    cache = RAW / "news"
    cache.mkdir(exist_ok=True)
    path = cache / f"{player_id}.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_h * 3600:
        feed = json.loads(path.read_text())
    else:
        try:
            feed = requests.get("https://site.api.espn.com/apis/fantasy/v2/games/fba/news/players",
                                params={"limit": 20, "playerId": player_id}, timeout=20).json().get("feed", [])
            path.write_text(json.dumps(feed))
        except Exception as e:
            print(f"[WARN] ESPN news for {player_id} failed: {e}")
            feed = json.loads(path.read_text()) if path.exists() else []
    out = [{"date": x.get("published", "")[:10], "headline": x.get("headline", ""),
            "story": (x.get("story") or x.get("description") or "").strip()}
           for x in feed if x.get("type") != "Media" and x.get("published", "") >= since]
    return sorted(out, key=lambda x: x["date"], reverse=True)


def start_rate(logs: pd.DataFrame, season: int) -> pd.Series:
    s = logs[logs.season == season]
    return s.groupby("player_id").GS.mean()


def annotate(cur: pd.DataFrame, logs: pd.DataFrame, board: pd.DataFrame) -> pd.DataFrame:
    """Add measured context columns + a list of tags to each current-season game log row."""
    cur = cur.copy()
    cur["team"] = [PRO_TEAM.get(int(t), "?") for t in cur.team_id]
    sb = scoreboard(cur.period.unique())
    ctx = [sb.get((t, int(p)), {}) for t, p in zip(cur.team, cur.period)]
    cur["margin"] = [c.get("margin") for c in ctx]
    cur["ot"] = [bool(c.get("ot")) for c in ctx]
    cur["opp"] = [c.get("opp") for c in ctx]
    cur["b2b"] = [(t, int(p) - 1) in sb for t, p in zip(cur.team, cur.period)]

    # rotation teammates who sat (projected 20+ MPG, on this team, no log that day)
    played = set(zip(cur.player_id, cur.period))
    rot = board[board.mpg >= 20][["name", "team", "mpg"]]
    by_team = {t: g for t, g in rot.groupby("team")}
    cur["teammates_out"] = [
        [r["name"] for pid, r in by_team.get(t, pd.DataFrame()).iterrows() if (pid, p) not in played and pid != me]
        for me, t, p in zip(cur.player_id, cur.team, cur.period)]

    last_start = start_rate(logs, cur_season(cur) - 1)
    exp = board[["mpg", "fg_pct", "ft_pct", "fppg"]]
    tags_all, luck_all = [], []
    for _, r in cur.iterrows():
        tags = []
        m = r.margin
        if m is not None and abs(m) >= BLOWOUT:
            tags.append(f"blowout {'win' if m > 0 else 'loss'} by {abs(m)}")
        if r.ot:
            tags.append("overtime")
        if r.PF >= 6 or r.DQ > 0:
            tags.append(f"fouled out ({int(r.PF)} PF)")
        elif r.PF >= FOUL_TROUBLE:
            tags.append(f"foul trouble ({int(r.PF)} PF)")
        if r.EJ > 0:
            tags.append("ejected")
        if r.TF > 0 or r.FF > 0:
            tags.append(f"tech/flagrant ({int(r.TF)}T {int(r.FF)}F)")
        if r.b2b:
            tags.append("2nd night of back-to-back")
        ls = last_start.get(r.player_id)
        if ls is not None and ls >= 0.6 and r.GS == 0:
            tags.append(f"came off bench (started {ls:.0%} last season)")
        elif ls is not None and ls <= 0.3 and r.GS == 1:
            tags.append(f"started (only {ls:.0%} last season)")
        if r.teammates_out:
            tags.append("teammates out: " + ", ".join(r.teammates_out[:3]))
        # shooting luck: points gained/lost vs the player's projected FG% and FT% on these attempts
        luck = 0.0
        if r.player_id in exp.index:
            e = exp.loc[r.player_id]
            pts_per_make = (r.PTS - r.FTM) / r.FGM if r.FGM else 2.2
            if r.FGA and e.fg_pct == e.fg_pct:
                luck += (r.FGM - e.fg_pct * r.FGA) * (2 + pts_per_make)   # FGM +2 and the points it scores
            if r.FTA and e.ft_pct == e.ft_pct:
                luck += (r.FTM - e.ft_pct * r.FTA) * 2                    # a make is +1 pt +1 FTM vs a miss
            if abs(luck) >= 8:
                tags.append(f"shooting {'hot' if luck > 0 else 'cold'} ({luck:+.0f} FP vs his normal %)")
        tags_all.append(tags)
        luck_all.append(round(luck, 1))
    cur["tags"] = tags_all
    cur["shoot_luck"] = luck_all
    # games that distort minutes in a way that says nothing about role
    cur["distorted"] = [any(t.startswith(("blowout", "overtime", "fouled out", "foul trouble", "ejected"))
                            for t in tags) for tags in tags_all]
    return cur


def cur_season(cur: pd.DataFrame) -> int:
    return int(cur.season.max()) if len(cur) else 2027
