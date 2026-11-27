#!/usr/bin/env python3
"""League B trade search: deals that raise my rest-of-season points and look fair to the other manager.

  python3 league_b_trades.py --give "Paul Reed" [--with "Saddiq Bey,Mikal Bridges"] [--team "Lavar"]

Every deal includes all --give players and optionally one --with player; I receive 1-2 players.
Value = projected points over the rest of the regular season from my best 12 players (blend of
projection and this-season form, times projected games left). Paul Reed is valued as a starter until
Jalen Duren returns (~2 weeks) and a backup after. Fairness = ESPN draft position (convex, star-weighted).
Max 3 centers by primary position, not counting a center on IR. Recommend only.
"""
from __future__ import annotations

import argparse
import itertools
import math

import pandas as pd

from src.common import PROCESSED, header, load_env, load_league
from src.espn_league import fetch_rosters
from performance import espn

ME = "Okc champs 2027"
REG_WEEKS = 19


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--give", required=True)
    ap.add_argument("--with", dest="with_", default="")
    ap.add_argument("--team", default="")
    ap.add_argument("--min-fair", type=float, default=-10, help="lowest name-value balance to show (negative = they lose on paper)")
    ap.add_argument("--realistic", action="store_true", help="only deals where what I get scores <= 110%% of what I give this season (FP/G)")
    args = ap.parse_args()
    cfg = load_league("league_b")
    print(header(cfg))
    b = pd.read_csv(PROCESSED / "league_b_board.csv").set_index("name")
    mp = espn("league_b", ["mStatus"])["status"]["currentMatchupPeriod"]
    weeks_left = max(REG_WEEKS - mp, 1)
    frac = weeks_left / 22.0

    def fpg(n):
        x = b.loc[n]
        gp = x.cur_gp if x.cur_gp == x.cur_gp else 0
        c = x.cur_fppg if x.cur_fppg == x.cur_fppg else x.fppg
        w = min(gp / 15, 0.6)
        return (1 - w) * x.fppg + w * c

    def season(n):
        if n == "Paul Reed":
            g = b.loc[n, "g_proj"] * frac
            return 7 * fpg(n) + max(g - 7, 0) * 25
        return fpg(n) * b.loc[n, "g_proj"] * frac * (0.0 if b.loc[n, "inj"] == "OUT" and n != "Jalen Duren" else 1.0) \
            if n != "Jalen Duren" else fpg(n) * b.loc[n, "g_proj"] * frac * 0.8

    def team_val(ros):
        return sum(sorted((season(n) for n in ros if n in b.index), reverse=True)[:12])

    nv = lambda ns: sum(100 * math.exp(-(b.loc[n, "adp"] if b.loc[n, "adp"] == b.loc[n, "adp"] else 150) / 40) for n in ns)
    R = fetch_rosters(cfg, load_env())["teams"]
    mine = [n for n in R[ME] if n in b.index]
    base = team_val(mine)
    give = [g.strip() for g in args.give.split(",") if g.strip()]
    extras = [[]] + [[w.strip()] for w in args.with_.split(",") if w.strip()]
    rows = []
    for opp, ros in R.items():
        if opp == ME or (args.team and args.team.lower() not in opp.lower()):
            continue
        their = [n for n in ros if n in b.index and b.loc[n, "inj"] != "OUT"]
        for ex in extras:
            g = give + ex
            for k in (1, 2):
                for q in itertools.combinations(their, k):
                    new = [n for n in mine if n not in g] + list(q)
                    cut = []
                    while len(new) > len(mine):
                        c = min((n for n in new if n not in q and n != "Jalen Duren"), key=season)
                        new.remove(c)
                        cut.append(c)
                    if sum(b.loc[n, "primary"] == "C" for n in new if n != "Jalen Duren") > 3:
                        continue
                    gain = (team_val(new) - base) / weeks_left
                    fair = nv(g) - nv(q)
                    if gain < 5 or fair < args.min_fair:
                        continue
                    cur = lambda ns: sum((b.loc[n, "cur_fppg"] if b.loc[n, "cur_fppg"] == b.loc[n, "cur_fppg"] else b.loc[n, "fppg"]) for n in ns)
                    cg, cq = cur(g), cur(q)
                    if args.realistic and cq > 1.10 * cg:
                        continue
                    rows.append((round(gain, 1), round(fair), opp, " + ".join(g), " + ".join(q), ", ".join(cut), f"{cg:.0f} vs {cq:.0f}"))
    rows.sort(reverse=True)
    print(f"\n{'gain FP/wk':>10} | {'fair':>5} | FP/G give vs get | team | you give -> you get | you cut")
    seen = {}
    for r in rows:
        if seen.get(r[2], 0) >= 3:
            continue
        seen[r[2]] = seen.get(r[2], 0) + 1
        print(f"{r[0]:>10} | {r[1]:>5} | {r[6]:>9} | {r[2]} | {r[3]} -> {r[4]}" + (f" | cut {r[5]}" if r[5] else ""))


if __name__ == "__main__":
    main()
