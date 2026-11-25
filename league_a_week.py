#!/usr/bin/env python3
"""League A weekly projection vs this week's opponent, and waiver claims that flip categories.

  python3 league_a_week.py                       # projection only
  python3 league_a_week.py --claims 2 --drops "Trey Murphy III,Paul Reed" [--open 1]

Categories so far come from game logs of each team's starters on finished days; the rest of the week
uses per-game rates of each team's 10 best healthy players per remaining day. --open N adds N claims
that need no drop (e.g. after moving an OUT player to IR). Claims process nightly at 21:00 HKT
(09:00 ET), before that day's games. Recommend only.
"""
from __future__ import annotations

import argparse
import itertools

from performance import espn, load
from src.categories import CATS, LeagueA
from src.common import header, load_league
from src.weekly import schedule

COLS = ['PTS', 'REB', 'AST', 'STL', 'BLK', '3PM', 'TO', 'FGM', 'FGA', 'FTM', 'FTA']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", type=int, default=0)
    ap.add_argument("--drops", default="")
    ap.add_argument("--open", type=int, default=0)
    args = ap.parse_args()
    cfg = load_league("league_a")
    print(header(cfg))
    days, games = schedule()
    L = LeagueA()
    P = L.players
    idmap = {int(P.at[i, 'id']): i for i in P.index}
    ix = {P.at[i, 'name']: i for i in P.index}
    _, _, cur, _ = load()
    d = espn('league_a', ['mRoster', 'mTeam', 'mStatus', 'mMatchupScore'])
    names = {t['id']: t['name'].strip() for t in d['teams']}
    mp, latest = d['status']['currentMatchupPeriod'], d['status']['latestScoringPeriod']
    me = cfg['my_team_name']
    opp = next(names[m[s]['teamId']] for m in d['schedule'] if m['matchupPeriodId'] == mp
               for s in ('home', 'away') if me in (names[m['home']['teamId']], names[m['away']['teamId']])
               and names[m[s]['teamId']] != me)
    week = days[latest].isocalendar()[1]
    done = [p for p in sorted(days) if p <= latest and days[p].isocalendar()[1] == week]
    left = [p for p in sorted(days) if p > latest and days[p].isocalendar()[1] == week]

    def so_far(tn):
        so = {c: 0.0 for c in COLS}
        for sp in done:
            t = next(x for x in espn('league_a', ['mRoster'], scoringPeriodId=sp)['teams'] if names[x['id']] == tn)
            for e in t['roster']['entries']:
                if e['lineupSlotId'] in (12, 13):
                    continue
                g = cur[(cur.player_id == e['playerPoolEntry']['player']['id']) & (cur.period == sp)]
                for c in COLS:
                    so[c] += float(g[c].sum()) if len(g) else 0
        return so

    def active(tn):
        t = next(x for x in d['teams'] if names[x['id']] == tn)
        return [idmap[e['playerPoolEntry']['player']['id']] for e in t['roster']['entries']
                if e['lineupSlotId'] != 13 and e['playerPoolEntry']['player']['id'] in idmap
                and P.at[idmap[e['playerPoolEntry']['player']['id']], 'inj'] not in ('OUT', 'INJURY_RESERVE')]

    def final(so, roster):
        f = dict(so)
        for p in left:
            for i in sorted([i for i in roster if p in games.get(P.at[i, 'team'], set())],
                            key=lambda i: -P.at[i, 'z_total'])[:10]:
                for c in COLS:
                    f[c] += float(P.at[i, f"{c.lower()}_pg"])
        f['FG%'] = f['FGM'] / f['FGA']
        f['FT%'] = f['FTM'] / f['FTA']
        return f

    beat = lambda x, y, c: (x < y) if c == 'TO' else (x > y)
    so_me, so_op = so_far(me), so_far(opp)
    mine, theirs = active(me), active(opp)
    fo = final(so_op, theirs)

    def report(roster, label):
        fm = final(so_me, roster)
        w = sum(beat(fm[c], fo[c], c) for c in CATS)
        return w, fm

    w, fm = report(mine, 'now')
    print(f"\nWeek {mp} vs {opp}; days left {[str(days[p])[5:] for p in left]}")
    for c in CATS:
        print(f"  {c:4} {fm[c]:8.3f} vs {fo[c]:8.3f}  {'WIN' if beat(fm[c], fo[c], c) else 'loss'}")
    print(f"  projected {w}-{9 - w}")
    if not args.claims:
        return
    drops = [ix[n.strip()] for n in args.drops.split(',') if n.strip()]
    pool = [i for i in L.fa.index[:200] if P.at[i, 'inj'] not in ('OUT', 'INJURY_RESERVE')
            and len([p for p in left if p in games.get(P.at[i, 'team'], set())]) >= 2]
    singles = []
    for f in pool:
        w1, _ = report(mine + [f], '')
        singles.append((w1, f))
    singles.sort(key=lambda x: -x[0])
    cand = [f for _, f in singles[:25]]
    best = []
    for adds in itertools.combinations(cand, args.claims):
        for dr in itertools.combinations(drops, max(0, args.claims - args.open)):
            roster = [i for i in mine if i not in dr] + list(adds)
            w2, f2 = report(roster, '')
            best.append((w2, sum(abs(f2[c] - fo[c]) / max(abs(fo[c]), 1e-9) * (1 if beat(f2[c], fo[c], c) else -1) for c in CATS),
                         [P.at[a, 'name'] for a in adds], [P.at[x, 'name'] for x in dr]))
    best.sort(key=lambda x: (-x[0], -x[1]))
    print(f"\nBest {args.claims} claim(s):")
    for b_ in best[:6]:
        print(f"  {b_[0]}-{9 - b_[0]}  add {', '.join(b_[2])}  drop {', '.join(b_[3]) or '(open spot)'}")


if __name__ == "__main__":
    main()
