#!/usr/bin/env python3
"""League B weekly plan: projection vs this week's opponent and a free-agent streaming plan.

  python3 league_b_week.py                         # rest of the current matchup week
  python3 league_b_week.py --stream "Saddiq Bey,Jrue Holiday" --adds 4

Players are valued at a blend of projection and this-season form (form weight grows with games
played, capped at 60%). Each day the best lineup that fits PG/SG/SF/PF/C/G/F + 3 UTIL is started.
The stream plan greedily swaps players named in --stream (and anyone added) for free agents,
choosing the drop day, so games fill otherwise empty lineup slots. Max 3 centers by primary position
(a center on IR does not count). Recommend only: nothing is changed on ESPN.
"""
from __future__ import annotations

import argparse

import pandas as pd

from src.common import PROCESSED, header, load_env, load_league
from src.espn_league import fetch_rosters
from src.weekly import fits, schedule
from performance import espn

ME = "Okc champs 2027"


def value(b: pd.DataFrame, n: str) -> float:
    x = b.loc[n]
    gp = x.cur_gp if x.cur_gp == x.cur_gp else 0
    cur = x.cur_fppg if x.cur_fppg == x.cur_fppg else x.fppg
    w = min(gp / 15, 0.6)
    return (1 - w) * x.fppg + w * cur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stream", default="", help="comma-separated players you are willing to stream away")
    ap.add_argument("--adds", type=int, default=4)
    ap.add_argument("--include-today", action="store_true", help="count today's games too (before they start)")
    args = ap.parse_args()
    cfg = load_league("league_b")
    print(header(cfg))
    b = pd.read_csv(PROCESSED / "league_b_board.csv").set_index("name")
    days, games = schedule()
    st = espn("league_b", ["mStatus", "mMatchupScore", "mTeam", "mRoster"])
    mp, latest = st["status"]["currentMatchupPeriod"], st["status"]["latestScoringPeriod"]
    names = {t["id"]: t["name"].strip() for t in st["teams"]}
    # matchup weeks are Monday-Sunday; take the days left in this week from the pro schedule
    first_day = latest if args.include_today else latest + 1
    PER = [p for p in sorted(days) if p >= first_day and days[p].isocalendar()[1] == days[latest].isocalendar()[1]]
    rosters = {names[t["id"]]: [e["playerPoolEntry"]["player"]["fullName"] for e in t["roster"]["entries"]
                                if e["lineupSlotId"] != 13] for t in st["teams"]}
    opp = next(names[m[s]["teamId"]] for m in st["schedule"] if m["matchupPeriodId"] == mp
               for s in ("home", "away") if ME in (names[m["home"]["teamId"]], names[m["away"]["teamId"]])
               and names[m[s]["teamId"]] != ME)
    score = {names[m[s]["teamId"]]: m[s].get("totalPointsLive") or 0 for m in st["schedule"]
             if m["matchupPeriodId"] == mp for s in ("home", "away")}
    fa = [n for n, s in fetch_rosters(cfg, load_env())["free_agents"] if s == "FREEAGENT" and n in b.index]

    def lineup(ns, p):
        pl = [n for n in ns if n in b.index and p in games.get(b.loc[n, "team"], set())
              and b.loc[n, "inj"] not in ("OUT", "INJURY_RESERVE", "SUSPENSION")]
        pl.sort(key=lambda n: -value(b, n))
        ch = []
        for n in pl:
            if fits([b.loc[c, "pos"].split("/") for c in ch] + [b.loc[n, "pos"].split("/")]):
                ch.append(n)
        return ch

    def total(held, ended):
        t = 0.0
        for p in PER:
            ns = [n for n, s in held.items() if s <= p] + [n for n, (s, e) in ended.items() if s <= p <= e]
            t += sum(value(b, n) for n in lineup(ns, p))
        return t

    me = [n for n in rosters[ME] if n in b.index]
    op = [n for n in rosters[opp] if n in b.index]
    first = PER[0]
    mine_rest = total({n: first for n in me}, {})
    opp_rest = total({n: first for n in op}, {})
    print(f"\nWeek {mp} vs {opp}: score {score.get(ME, 0)} - {score.get(opp, 0)}; "
          f"rest of week (days {', '.join(str(days[p])[5:] for p in PER)}) projected {mine_rest:.0f} vs {opp_rest:.0f}")
    print("  my players per day:", [(str(days[p])[5:], len(lineup(me, p))) for p in PER],
          "| opp:", [(str(days[p])[5:], len(lineup(op, p))) for p in PER])
    print(f"  projected final {score.get(ME, 0) + mine_rest:.0f} vs {score.get(opp, 0) + opp_rest:.0f}")

    stream = {s.strip() for s in args.stream.split(",") if s.strip()}
    held, ended, cur = {n: first for n in me}, {}, mine_rest
    ncen = lambda h: sum(b.loc[n, "primary"] == "C" for n in h)
    pool = [f for f in fa if b.loc[f, "inj"] not in ("OUT", "INJURY_RESERVE", "SUSPENSION")
            and (b.loc[f, "cur_gp"] or 0) >= 3 and b.loc[f, "mpg"] >= 20 and b.loc[f, "cur_fppg"] >= 0.8 * b.loc[f, "fppg"]]
    plan = []
    for _ in range(args.adds):
        best = None
        for d in [n for n in held if n in stream]:
            for day in PER:
                if held[d] > day:
                    continue
                for f in pool:
                    if f in held or f in ended:
                        continue
                    h, e = dict(held), dict(ended)
                    e[d] = (h.pop(d), day - 1)
                    h[f] = day
                    if ncen(h) > 3:
                        continue
                    t = total(h, e)
                    if best is None or t > best[0]:
                        best = (t, d, f, day, h, e)
        if best is None or best[0] - cur < 8:
            break
        t, d, f, day, held, ended = best
        stream.add(f)
        plan.append((str(days[day])[5:], d, f, b.loc[f, "team"], round(value(b, f), 1), round(t - cur, 1)))
        cur = t
    print("\nStream plan (date = first day the new player counts; drop the old one before that day's games):")
    for x in plan:
        print(f"  {x[0]}: drop {x[1]} -> add {x[2]} ({x[3]}, {x[4]} FP/G)  +{x[5]}")
    print(f"  rest of week with streams: {cur:.0f}  -> projected final {score.get(ME, 0) + cur:.0f} vs {score.get(opp, 0) + opp_rest:.0f}")


if __name__ == "__main__":
    main()
