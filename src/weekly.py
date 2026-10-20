"""League B weekly projection: games per player in a matchup week and a daily lineup optimizer."""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd

from .common import RAW
from .draft import REQUIRED, SLOT_OK

ET = dt.timezone(dt.timedelta(hours=-4))
ABBR = {"SA": "SAS", "NO": "NOP", "NY": "NYK", "GS": "GSW", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}


def schedule() -> tuple[dict[int, dt.date], dict[str, set[int]]]:
    """(scoring period -> date, team abbrev -> scoring periods with a game)."""
    g = json.load(open(RAW / "pro_schedule_2027.json"))
    days, games = {}, {}
    for t in g["settings"]["proTeams"]:
        ab = ABBR.get(t["abbrev"].upper(), t["abbrev"].upper())
        for k, gl in (t.get("proGamesByScoringPeriod") or {}).items():
            for x in gl:
                days[int(k)] = dt.datetime.fromtimestamp(x["date"] / 1000, ET).date()
                games.setdefault(ab, set()).add(int(k))
    return days, games


def fits(lineup_pos: list[list[str]], n_slots: int = 10) -> bool:
    """Can these players all start? Needs the 7 position slots fillable + total <= 10."""
    if len(lineup_pos) > n_slots:
        return False
    # every player must take a distinct slot among 7 specific + 3 UTIL
    slots = REQUIRED + ["UTIL"] * (n_slots - len(REQUIRED))
    match = {}

    def ok(pos, slot):
        return slot == "UTIL" or bool(set(pos) & SLOT_OK[slot])

    def assign(i, seen):
        for j, s in enumerate(slots):
            if j in seen or not ok(lineup_pos[i], s):
                continue
            seen.add(j)
            if j not in match or assign(match[j], seen):
                match[j] = i
                return True
        return False

    return all(assign(i, set()) for i in range(len(lineup_pos)))


def week_projection(roster: pd.DataFrame, periods: list[int], games: dict[str, set[int]]) -> pd.DataFrame:
    """Per-player games, starts and projected points for the week with a greedy daily lineup."""
    out = roster.copy()
    out["games"] = [sum(p in games.get(t, set()) for p in periods) for t in out.team]
    out["starts"] = 0
    out_now = out.inj.fillna("").isin(["OUT", "SUSPENSION", "INJURY_RESERVE"])
    for p in periods:
        has_game = pd.Series([p in games.get(t, set()) for t in out.team], index=out.index)
        playing = out[has_game & ~out_now].sort_values("fppg", ascending=False)
        chosen = []
        for pid, r in playing.iterrows():
            if fits([out.at[c, "pos"].split("/") for c in chosen] + [r.pos.split("/")]):
                chosen.append(pid)
        out.loc[chosen, "starts"] += 1
    out["proj_pts"] = (out.starts * out.fppg).round(1)
    return out
