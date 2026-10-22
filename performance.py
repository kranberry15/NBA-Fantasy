#!/usr/bin/env python3
"""Judge the model and our moves against what actually happened in 2026-27.

  python3 performance.py            # writes reports/performance_<date>.md and prints it

1. Model scorecard: preseason projections (ours, ESPN's, "repeat last season") vs actual FP/G.
   Everyone is graded against the frozen preseason board, so later updates can't flatter us.
2. Hot / cold: actual vs preseason for my players, with a sample-size check so one game doesn't fool us.
3. Minutes watch: role changes show up in minutes long before they show up in averages.
4. Move scorecard: every executed trade / pickup, actual production of players in vs players out since the move.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import sys
from datetime import date

import numpy as np
import pandas as pd
import requests

from src.common import DATA, PROCESSED, REPORTS, load_env, load_league, norm_name
from src.espn_league import URL, _cookies, all_transactions
from src.projections import fantasy_points

SEASON = 2027
DAY1 = dt.date(2026, 10, 20)          # scoring period 1 (US date)
ET = dt.timezone(dt.timedelta(hours=-4))
CATS = {"PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK", "3PM": "3PM", "TO": "TO"}


ABBR = {"NY": "NYK", "SA": "SAS", "GS": "GSW", "NO": "NOP", "UTAH": "UTA", "WSH": "WAS", "PHO": "PHX"}
BLOWOUT = 20      # final margin at which starters sit and bench minutes balloon


def margins(periods) -> dict[tuple[str, int], int]:
    """(team, scoring period) -> final margin, from ESPN's public scoreboard; cached in data/raw."""
    import json
    cache = DATA / "raw" / "margins.json"
    have = json.loads(cache.read_text()) if cache.exists() else {}
    for sp in periods:
        day = DAY1 + dt.timedelta(days=int(sp) - 1)
        if str(sp) in have or day >= date.today():
            continue
        try:
            r = requests.get("https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
                             params={"dates": day.strftime("%Y%m%d")}, timeout=20).json()
        except Exception as e:
            print(f"[WARN] scoreboard {day} failed: {e}")
            continue
        games = {}
        for ev in r.get("events", []):
            if ev["status"]["type"].get("completed"):
                c = ev["competitions"][0]["competitors"]
                m = abs(int(c[0]["score"]) - int(c[1]["score"]))
                for x in c:
                    games[ABBR.get(x["team"]["abbreviation"], x["team"]["abbreviation"])] = m
        have[str(sp)] = games
    cache.write_text(json.dumps(have))
    return {(t, int(sp)): m for sp, g in have.items() for t, m in g.items()}


def load():
    W = load_league("league_b")["scoring"]
    pre = pd.read_csv(PROCESSED / "history" / "league_b_board_preseason.csv").set_index("id")
    now = pd.read_csv(PROCESSED / "league_b_board.csv").set_index("id")
    with sqlite3.connect(DATA / "cache.sqlite") as con:
        logs = pd.read_sql("select * from game_logs", con)
    logs["FP"] = fantasy_points(logs, W)
    cur = logs[logs.season == SEASON].copy()
    from src.common import PRO_TEAM
    mg = margins(sorted(cur.period.unique()))
    cur["margin"] = [mg.get((PRO_TEAM.get(int(t), "?"), int(p))) for t, p in zip(cur.team_id, cur.period)]
    cur["blowout"] = cur.margin.fillna(0) >= BLOWOUT
    last = logs[logs.season == SEASON - 1]
    sd = last.groupby("player_id").FP.std()            # each player's game-to-game swing, last season
    return pre, now, cur, sd


def player_table(pre, cur, sd) -> pd.DataFrame:
    g = cur.groupby("player_id").agg(gp=("FP", "size"), fp=("FP", "sum"), mins=("MIN", "sum"),
                                     blow=("blowout", "sum"))
    close = cur[~cur.blowout].groupby("player_id").MIN.mean().rename("close_mpg")
    g = g.join(close)
    t = g.join(pre[["name", "team", "fppg", "espn_fppg_pre", "last_fppg", "mpg"]], how="inner")
    t["act_fppg"] = t.fp / t.gp
    t["act_mpg"] = t.mins / t.gp
    t["diff"] = t.act_fppg - t.fppg
    s = sd.reindex(t.index).fillna(0.35 * t.fppg.clip(lower=10))
    t["z"] = t["diff"] / (s / np.sqrt(t.gp))          # how surprising, given sample size
    return t


def scorecard(t: pd.DataFrame) -> list[str]:
    out = ["## 1. Model scorecard (fantasy points per game, League B scoring)", ""]
    n_g = int(t.gp.sum())
    if len(t) == 0:
        return out + ["No 2026-27 games yet.", ""]
    rows = []
    for col, label in (("fppg", "Our preseason projection"), ("espn_fppg_pre", "ESPN projection*"),
                       ("last_fppg", "Repeat last season")):
        x = t[t[col].notna() & t.fppg.notna()]
        err = (x.act_fppg - x[col])
        w = x.gp
        rows.append((label, len(x), (err.abs() * w).sum() / w.sum(), (err * w).sum() / w.sum()))
    out += ["| Method | Players | Avg miss (FP/G, games-weighted) | Bias (+ = players beat it) |", "|---|---|---|---|"]
    out += [f"| {l} | {n} | {m:.2f} | {b:+.2f} |" for l, n, m, b in rows]
    both = t[t.espn_fppg_pre.notna()]
    closer = int(((both.act_fppg - both.fppg).abs() < (both.act_fppg - both.espn_fppg_pre).abs()).sum())
    out += ["", f"Ours closer than ESPN for {closer} of {len(both)} players.",
            f"Sample: {len(t)} players, {n_g} player-games. "
            + ("**Too early to judge (need ~2-3 weeks); single games swing ±15 FP.**" if n_g < 1500 else ""),
            "", "*ESPN's line converted with League B weights; OREB/DD/TD/TF it doesn't project are filled from "
            "last season. ESPN may have refreshed its projections after opening night, which can only help it.", ""]
    return out


def hot_cold(t: pd.DataFrame, names: list[str], title: str) -> list[str]:
    keys = {norm_name(n) for n in names}
    x = t[t.name.map(norm_name).isin(keys)].sort_values("diff", ascending=False)
    out = [f"## {title}", "", "| Player | G (blowouts) | Actual FP/G | Projected | Diff | Verdict | MPG act/proj | MPG in close games |",
           "|---|---|---|---|---|---|---|---|"]
    for _, r in x.iterrows():
        if r.gp >= 3 and abs(r.z) >= 2:
            verdict = "REAL change" if r["diff"] > 0 else "REAL drop"
        elif abs(r.z) >= 2:
            verdict = "watch (small sample)"
        else:
            verdict = "noise"
        close = f"{r.close_mpg:.0f}" if r.close_mpg == r.close_mpg else "-"
        out.append(f"| {r['name']} | {r.gp} ({int(r.blow)}) | {r.act_fppg:.1f} | {r.fppg:.1f} | {r['diff']:+.1f} | {verdict} | "
                   f"{r.act_mpg:.0f}/{r.mpg:.0f} | {close} |")
    missing = sorted(set(names) - set(x.name))
    if missing:
        out.append(f"\nNo game yet: {', '.join(missing)}")
    return out + [""]


def minutes_watch(t: pd.DataFrame) -> list[str]:
    """Role changes, judged on minutes in non-blowout games only (blowouts bench starters)."""
    x = t[t.close_mpg.notna()].copy()
    x["dm"] = x.close_mpg - x.mpg
    x = x[x.dm.abs() >= 6].sort_values("dm", ascending=False)
    out = ["## Minutes watch (non-blowout games only; a blowout = final margin 20+)", "",
           "| Player | Team | G (blowouts) | MPG close games | MPG projected | Change | Trust |", "|---|---|---|---|---|---|---|"]
    for _, r in pd.concat([x.head(8), x.tail(5)]).drop_duplicates().iterrows():
        close_g = int(r.gp - r.blow)
        trust = "act" if close_g >= 3 else "watch" if close_g == 2 else "1 game - wait"
        out.append(f"| {r['name']} | {r.team} | {r.gp} ({int(r.blow)}) | {r.close_mpg:.1f} | {r.mpg:.1f} | {r.dm:+.1f} | {trust} |")
    return out + [""]


def espn(key: str, views) -> dict:
    cfg = load_league(key)
    return requests.get(URL.format(season=cfg["season"], lid=cfg["league_id"]),
                        params=[("view", v) for v in views], cookies=_cookies(load_env()), timeout=30).json()


def rosters(key: str) -> list[str]:
    cfg = load_league(key)
    d = espn(key, ["mRoster", "mTeam"])
    t = next(x for x in d["teams"] if norm_name(x["name"]) == norm_name(cfg["my_team_name"]))
    return [e["playerPoolEntry"]["player"]["fullName"] for e in t["roster"]["entries"]]


def moves(key: str, cur: pd.DataFrame, pre: pd.DataFrame) -> list[str]:
    """Players in vs out since each executed move (my team only)."""
    cfg = load_league(key)
    d, tx = all_transactions(cfg, load_env())
    me = next(t["id"] for t in d["teams"] if norm_name(t["name"]) == norm_name(cfg["my_team_name"]))
    out = [f"## Move scorecard - {cfg['league_name']}", ""]
    rows = 0
    for x in sorted(tx, key=lambda x: x.get("processDate") or x["proposedDate"]):
        if x.get("status") != "EXECUTED" or x["type"] not in ("TRADE_ACCEPT", "WAIVER", "FREEAGENT"):
            continue
        items = x.get("items", [])
        ins = [i["playerId"] for i in items if i.get("toTeamId") == me]
        outs = [i["playerId"] for i in items if i.get("fromTeamId") == me]
        if not ins and not outs:
            continue
        when = dt.datetime.fromtimestamp((x.get("processDate") or x["proposedDate"]) / 1000, ET)
        start = (when.date() - DAY1).days + 1                 # first scoring period that can count
        since = cur[cur.period >= start]

        def side(ids):
            s = since[since.player_id.isin(ids)]
            names = ", ".join(pre.name.get(i, str(i)) for i in ids)
            return names, s
        n_in, s_in = side(ins)
        n_out, s_out = side(outs)
        head = f"**{when:%a %d %b}** {x['type'].replace('_ACCEPT', '')}: IN {n_in or '-'} / OUT {n_out or '-'}"
        if cfg["format"] == "points":
            out.append(f"- {head} -> since then IN {s_in.FP.sum():.1f} pts ({len(s_in)} games) vs "
                       f"OUT {s_out.FP.sum():.1f} pts ({len(s_out)} games): **{s_in.FP.sum() - s_out.FP.sum():+.1f}**")
        else:
            def line(s):
                fg = s.FGM.sum() / s.FGA.sum() if s.FGA.sum() else float("nan")
                ft = s.FTM.sum() / s.FTA.sum() if s.FTA.sum() else float("nan")
                return {**{k: int(s[v].sum()) for k, v in CATS.items()}, "FG%": round(float(fg), 3), "FT%": round(float(ft), 3), "G": len(s)}
            out.append(f"- {head}\n  - IN:  {line(s_in)}\n  - OUT: {line(s_out)}")
        rows += 1
    if not rows:
        out.append("No executed moves yet.")
    return out + ["", "_A move is judged on actual production since it happened; give it 2-3 weeks before calling it._", ""]


def main():
    pre, now, cur, sd = load()
    t = player_table(pre, cur, sd)
    lines = [f"# Performance report {date.today()}", "",
             f"Season games so far: {cur.period.max() if len(cur) else 0} days, {len(cur)} player-games.", ""]
    lines += scorecard(t)
    for key, title in (("league_b", "2. Hot / cold - Ball Don't Lie (my roster)"),
                       ("league_a", "2b. Hot / cold - BLR Dreaming FNBA (my roster)")):
        try:
            lines += hot_cold(t, rosters(key), title)
        except Exception as e:
            lines += [f"## {title}", f"ESPN roster fetch failed: {e}", ""]
    lines += minutes_watch(t)
    for key in ("league_b", "league_a"):
        try:
            lines += moves(key, cur, pre)
        except Exception as e:
            lines += [f"Move scorecard for {key} failed: {e}", ""]
    text = "\n".join(lines)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"performance_{date.today()}.md"
    path.write_text(text)
    print(text)
    print(f"\nSaved {path}")


if __name__ == "__main__":
    sys.exit(main())
