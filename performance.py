#!/usr/bin/env python3
"""Judge the model, the players and my own management against what actually happened.

  python3 performance.py            # writes reports/performance_<date>.md and prints it

1. Model scorecard: frozen preseason projections (ours, ESPN's, "repeat last season") vs actual FP/G.
2. Player check (my rosters): every game with measured context - blowout, OT, fouls / fouled out,
   ejection, techs, back-to-back, started or bench, teammates out, shooting luck - plus ESPN news.
   Minutes are judged only on undistorted games; a verdict needs enough games.
3. Minutes watch (all players): role changes on undistorted games, with start-rate evidence.
4. Matchup review (my teams): points scored by my starters vs points left on the bench.
5. Move scorecard: every executed trade / pickup, actual production of players in vs out since the move.
No guessing: when a swing has no measured cause the report says "no measured cause".
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
from src.gamecontext import annotate, news, period_date, start_rate
from src.projections import fantasy_points

SEASON = 2027
ET = dt.timezone(dt.timedelta(hours=-4))
CATS = {"PTS": "PTS", "REB": "REB", "AST": "AST", "STL": "STL", "BLK": "BLK", "3PM": "3PM", "TO": "TO"}
SLOT = {0: "PG", 1: "SG", 2: "SF", 3: "PF", 4: "C", 5: "G", 6: "F", 11: "UTIL", 12: "BE", 13: "IR"}


# ------------------------------------------------------------------ data
def load():
    W = load_league("league_b")["scoring"]
    pre = pd.read_csv(PROCESSED / "history" / "league_b_board_preseason.csv").set_index("id")
    with sqlite3.connect(DATA / "cache.sqlite") as con:
        logs = pd.read_sql("select * from game_logs", con)
    logs["FP"] = fantasy_points(logs, W)
    cur = annotate(logs[logs.season == SEASON], logs, pre)
    sd = logs[logs.season == SEASON - 1].groupby("player_id").FP.std()
    return pre, logs, cur, sd


def espn(key: str, views, **params) -> dict:
    cfg = load_league(key)
    p = [("view", v) for v in views] + list(params.items())
    return requests.get(URL.format(season=cfg["season"], lid=cfg["league_id"]), params=p,
                        cookies=_cookies(load_env()), timeout=30).json()


def my_team(key: str, d: dict) -> dict:
    cfg = load_league(key)
    return next(t for t in d["teams"] if norm_name(t["name"]) == norm_name(cfg["my_team_name"]))


# ------------------------------------------------------------------ 1. model
def player_table(pre, cur, sd) -> pd.DataFrame:
    g = cur.groupby("player_id").agg(gp=("FP", "size"), fp=("FP", "sum"), mins=("MIN", "sum"),
                                     distorted=("distorted", "sum"), luck=("shoot_luck", "sum"), gs=("GS", "mean"))
    clean = cur[~cur.distorted].groupby("player_id").MIN.mean().rename("clean_mpg")
    t = g.join(clean).join(pre[["name", "team", "fppg", "espn_fppg_pre", "last_fppg", "mpg"]], how="inner")
    t["act_fppg"] = t.fp / t.gp
    t["act_mpg"] = t.mins / t.gp
    t["diff"] = t.act_fppg - t.fppg
    s = sd.reindex(t.index).fillna(0.35 * t.fppg.clip(lower=10))
    t["z"] = t["diff"] / (s / np.sqrt(t.gp))
    return t


def scorecard(t: pd.DataFrame) -> list[str]:
    out = ["## 1. Model scorecard (fantasy points per game, League B scoring)", ""]
    if len(t) == 0:
        return out + ["No 2026-27 games yet.", ""]
    rows = []
    for col, label in (("fppg", "Our preseason projection"), ("espn_fppg_pre", "ESPN projection*"),
                       ("last_fppg", "Repeat last season")):
        x = t[t[col].notna()]
        err, w = x.act_fppg - x[col], x.gp
        rows.append((label, len(x), (err.abs() * w).sum() / w.sum(), (err * w).sum() / w.sum()))
    out += ["| Method | Players | Avg miss (FP/G, games-weighted) | Bias (+ = players beat it) |", "|---|---|---|---|"]
    out += [f"| {l} | {n} | {m:.2f} | {b:+.2f} |" for l, n, m, b in rows]
    both = t[t.espn_fppg_pre.notna()]
    closer = int(((both.act_fppg - both.fppg).abs() < (both.act_fppg - both.espn_fppg_pre).abs()).sum())
    n_g = int(t.gp.sum())
    out += ["", f"Ours closer than ESPN for {closer} of {len(both)} players. Sample: {len(t)} players, {n_g} player-games."
            + (" **Too early to judge (need ~1,500+ player-games, about 2-3 weeks).**" if n_g < 1500 else ""),
            f"Shooting luck across all games: {t.luck.sum():+.0f} FP (drifts toward 0 as samples grow).",
            "", "*ESPN's line converted with League B weights; OREB/DD/TD/TF it does not project are filled from "
            "last season. ESPN may have updated its projections after opening night, which can only help it.", ""]
    return out


# ------------------------------------------------------------------ 2. players
def verdict(r) -> str:
    if r.gp >= 3 and abs(r.z) >= 2:
        return ("REAL rise" if r["diff"] > 0 else "REAL drop") + f" ({int(r.gp)} G)"
    if abs(r.z) >= 2:
        return "watch - small sample"
    if int(r.gp - r.distorted) == 0:
        return "no undistorted game yet"
    return "within normal range"


def player_check(t, cur, logs, names: list[str], title: str) -> list[str]:
    keys = {norm_name(n) for n in names}
    x = t[t.name.map(norm_name).isin(keys)].sort_values("diff", ascending=False)
    last_gs = start_rate(logs, SEASON - 1)
    out = [f"## {title}", "",
           "| Player | G | FP/G act / proj | Verdict | MPG act / proj (undistorted) | Started now / last season | Shooting luck |",
           "|---|---|---|---|---|---|---|"]
    for pid, r in x.iterrows():
        clean = f"{r.clean_mpg:.0f}" if r.clean_mpg == r.clean_mpg else "-"
        ls = last_gs.get(pid)
        ls_txt = f"{ls:.0%}" if ls is not None and ls == ls else "-"
        out.append(f"| {r['name']} | {int(r.gp)} | {r.act_fppg:.1f} / {r.fppg:.1f} | {verdict(r)} | "
                   f"{r.act_mpg:.0f} / {r.mpg:.0f} ({clean}) | {r.gs:.0%} / {ls_txt} | {r.luck:+.0f} FP |")
    out += ["", "**Game by game (measured context only):**", ""]
    for pid, r in x.iterrows():
        for _, g in cur[cur.player_id == pid].sort_values("period").iterrows():
            tags = "; ".join(g.tags) if g.tags else "no measured cause"
            out.append(f"- {r['name']} {period_date(g.period):%a %d %b} vs {g.opp}: {g.FP:.1f} FP in {g.MIN:.0f} min "
                       f"({g.FP - r.fppg:+.0f} FP, {g.MIN - r.mpg:+.0f} min vs proj; {int(g.PF)} PF, "
                       f"{'started' if g.GS else 'bench'}) - {tags}")
        for n in news(int(pid))[:1]:
            out.append(f"  - ESPN news {n['date']}: {n['headline']} {n['story'][:260]}")
    missing = sorted(set(names) - set(x.name))
    if missing:
        out.append(f"\nNo game yet: {', '.join(missing)}")
    return out + [""]


# ------------------------------------------------------------------ 3. minutes
def minutes_watch(t: pd.DataFrame) -> list[str]:
    x = t[t.clean_mpg.notna()].copy()
    x["dm"] = x.clean_mpg - x.mpg
    x = x[x.dm.abs() >= 6].sort_values("dm", ascending=False)
    out = ["## 3. Minutes watch (undistorted games only: no blowout, OT, foul trouble or ejection)", "",
           "| Player | Team | G (distorted) | MPG | Projected | Change | Started | Trust |", "|---|---|---|---|---|---|---|---|"]
    for _, r in pd.concat([x.head(10), x.tail(6)]).drop_duplicates().iterrows():
        clean_g = int(r.gp - r.distorted)
        trust = "act" if clean_g >= 3 else "watch" if clean_g == 2 else "1 game - wait"
        out.append(f"| {r['name']} | {r.team} | {int(r.gp)} ({int(r.distorted)}) | {r.clean_mpg:.1f} | {r.mpg:.1f} | "
                   f"{r.dm:+.1f} | {r.gs:.0%} | {trust} |")
    return out + [""]


# ------------------------------------------------------------------ 4. my management
def matchup_review(key: str, cur: pd.DataFrame) -> list[str]:
    """Points my starters scored vs points left on my bench on days a starting slot sat empty."""
    cfg = load_league(key)
    base = espn(key, ["mTeam", "mStatus"])
    week = (base.get("status") or {}).get("currentMatchupPeriod", 1)
    latest = (base.get("status") or {}).get("latestScoringPeriod", 1)
    W = load_league("league_b")["scoring"]
    out = [f"## 4. Lineup review - {cfg['league_name']} (week {week}, completed days)", ""]
    started_fp = bench_fp = left = 0.0
    lines = []
    for sp in range(1, latest):
        t = my_team(key, espn(key, ["mRoster", "mTeam"], scoringPeriodId=sp))
        idle, bench = 0, []
        for e in t["roster"]["entries"]:
            pid = e["playerPoolEntry"]["player"]["id"]
            g = cur[(cur.player_id == pid) & (cur.period == sp)]
            fp = float(fantasy_points(g, W).sum()) if len(g) else None
            if SLOT.get(e["lineupSlotId"]) in ("BE", "IR"):
                if fp is not None:
                    bench.append((e["playerPoolEntry"]["player"]["fullName"], fp))
                    bench_fp += fp
            elif fp is None:
                idle += 1
            else:
                started_fp += fp
        bench.sort(key=lambda x: -x[1])
        lost = sum(fp for _, fp in bench[:idle])   # 3 UTIL slots make these swaps legal in practice
        left += lost
        if bench:
            lines.append(f"- {period_date(sp):%a %d %b}: bench played " + ", ".join(f"{n} {fp:.1f}" for n, fp in bench)
                         + f"; empty starting slots {idle} -> "
                         + (f"**{lost:.1f} FP left on bench**" if lost else "nothing lost"))
    unit = "FP" if cfg["format"] == "points" else "FP-equivalent (size of the miss, League B scale)"
    out.append(f"Starters: {started_fp:.1f} {unit}. On my bench: {bench_fp:.1f}. "
               f"**Left on the bench while a starting slot was empty: {left:.1f}.**")
    out += lines or ["- No bench player has played yet."]
    return out + [""]


def moves(key: str, cur: pd.DataFrame, pre: pd.DataFrame) -> list[str]:
    cfg = load_league(key)
    d, tx = all_transactions(cfg, load_env())
    me = my_team(key, d)["id"]
    out = [f"## 5. Move scorecard - {cfg['league_name']}", ""]
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
        start = (when.date() - period_date(1)).days + 1
        since = cur[cur.period >= start]
        s_in, s_out = since[since.player_id.isin(ins)], since[since.player_id.isin(outs)]
        n_in = ", ".join(pre.name.get(i, str(i)) for i in ins) or "-"
        n_out = ", ".join(pre.name.get(i, str(i)) for i in outs) or "-"
        head = f"**{when:%a %d %b}** {x['type'].replace('_ACCEPT', '')}: IN {n_in} / OUT {n_out}"
        if cfg["format"] == "points":
            out.append(f"- {head} -> since then IN {s_in.FP.sum():.1f} pts ({len(s_in)} G, shooting luck "
                       f"{s_in.shoot_luck.sum():+.0f}) vs OUT {s_out.FP.sum():.1f} pts ({len(s_out)} G): "
                       f"**{s_in.FP.sum() - s_out.FP.sum():+.1f}**")
        else:
            def line(s):
                fg = s.FGM.sum() / s.FGA.sum() if s.FGA.sum() else float("nan")
                ft = s.FTM.sum() / s.FTA.sum() if s.FTA.sum() else float("nan")
                return {**{k: int(s[v].sum()) for k, v in CATS.items()}, "FG%": round(float(fg), 3),
                        "FT%": round(float(ft), 3), "G": len(s)}
            out.append(f"- {head}\n  - IN:  {line(s_in)}\n  - OUT: {line(s_out)}")
        rows += 1
    if not rows:
        out.append("No executed moves yet.")
    return out + ["", "_Judged on actual production since the move; give it 2-3 weeks before calling it._", ""]


# ------------------------------------------------------------------ main
def rosters(key: str) -> list[str]:
    d = espn(key, ["mRoster", "mTeam"])
    return [e["playerPoolEntry"]["player"]["fullName"] for e in my_team(key, d)["roster"]["entries"]]


def main():
    pre, logs, cur, sd = load()
    t = player_table(pre, cur, sd)
    lines = [f"# Performance report {date.today()}", "",
             f"Season so far: {int(cur.period.max()) if len(cur) else 0} days, {len(cur)} player-games. "
             "Every tag is measured (ESPN box scores, scoreboard, news); nothing is inferred.", ""]
    lines += scorecard(t)
    for key, title in (("league_b", "2. Player check - Ball Don't Lie (my roster)"),
                       ("league_a", "2b. Player check - BLR Dreaming FNBA (my roster)")):
        try:
            lines += player_check(t, cur, logs, rosters(key), title)
        except Exception as e:
            lines += [f"## {title}", f"ESPN roster fetch failed: {e}", ""]
    lines += minutes_watch(t)
    for key in ("league_b", "league_a"):
        try:
            lines += matchup_review(key, cur)
        except Exception as e:
            lines += [f"Lineup review for {key} failed: {e}", ""]
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
