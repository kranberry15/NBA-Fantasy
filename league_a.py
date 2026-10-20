#!/usr/bin/env python3
"""League A (9-cat) report: category profile, waiver pickups, trade ideas. Recommend-only.

  python league_a.py            # writes reports/league_a_<date>.md and prints it
"""
from __future__ import annotations

import sys
from datetime import date

import pandas as pd

from src.categories import CATS, LeagueA
from src.common import REPORTS, header, load_league


def cat_ranks(L: LeagueA, team: str, totals: dict) -> dict:
    ranks = {}
    for c in CATS:
        others = [t[c] for o, t in L.totals.items() if o != team]
        better = sum((o < totals[c]) if c == "TO" else (o > totals[c]) for o in others)
        ranks[c] = better + 1
    return ranks


def fmt_delta(L, team, after) -> str:
    before = L.totals[team]
    rb, ra = cat_ranks(L, team, before), cat_ranks(L, team, after)
    parts = []
    for c in CATS:
        d = after[c] - before[c]
        if "%" in c:
            if abs(d) >= 0.002:
                parts.append(f"{c} {d * 100:+.1f}pt (rank {rb[c]}->{ra[c]})")
        elif abs(d) >= 0.5:
            parts.append(f"{c} {d:+.1f}/wk (rank {rb[c]}->{ra[c]})")
    return "; ".join(parts)


def main():
    cfg = load_league("league_a")
    L = LeagueA(cfg["my_team_name"])
    P = L.players
    me = L.me
    out = [f"# {header(cfg)}", f"_Report {date.today()} - recommend only, nothing is done without your OK._", ""]

    t, _ = L.cat_table()
    ranks = pd.DataFrame({team: cat_ranks(L, team, L.totals[team]) for team in L.totals}).T[CATS]
    out += ["## 1. Category ranks (projected weekly totals, 1 = best of 6)", "",
            ranks.to_markdown(), "", "My projected week: " +
            ", ".join(f"{c} {L.totals[me][c]:.3f}" if "%" in c else f"{c} {L.totals[me][c]:.0f}" for c in CATS), ""]

    r = L.roster(me).sort_values("z_total", ascending=False)
    cols = ["name", "pos", "g_proj"] + [f"z_{c}" for c in CATS] + ["z_total"]
    out += ["## 2. My roster - per-game z-scores (FG%/FT% by volume impact, TO inverted)", "",
            r[cols].round(2).to_markdown(index=False), ""]

    out += ["## 3. Waiver pickups (FAAB 200, min bid 1, runs Sunday 6 AM ET)", "",
            "| # | Add | Drop | Gain (cat-wins/wk vs field) | What changes |", "|---|---|---|---|---|"]
    fa = L.fa_moves(me, n=8)
    for i, m in enumerate(fa.itertuples(), 1):
        after = L.with_swap(me, [m.drop], [m.add])
        out.append(f"| {i} | {P.at[m.add, 'name']} ({P.at[m.add, 'team']}, z {P.at[m.add, 'z_total']:+.2f}) | "
                   f"{P.at[m.drop, 'name']} (z {P.at[m.drop, 'z_total']:+.2f}) | {m.gain:+.2f} | {fmt_delta(L, me, after)} |")
    out += ["", "Bid guide: early season, spend <=5% of budget (1-10) on these; save the bulk for real role changes "
            "(injury to a starter, trade) later.", ""]

    out += ["## 4. Trade ideas (you gain in your weak cats, they gain in theirs, they get the bigger names by ADP)", "",
            "| # | Team | You give | You get | Your gain | Their gain | Your category changes |",
            "|---|---|---|---|---|---|---|"]
    tr = L.trades(me, n=10, max_size=2)
    for i, x in enumerate(tr.itertuples(), 1):
        after = L.with_swap(me, x.give, x.get)
        g = ", ".join(f"{P.at[j, 'name']} (ADP {P.at[j, 'adp']:.0f})" for j in x.give)
        q = ", ".join(f"{P.at[j, 'name']} (ADP {P.at[j, 'adp']:.0f})" for j in x.get)
        out.append(f"| {i} | {x.opp} | {g} | {q} | {x.my_gain:+.2f} | {x.their_gain:+.2f} | {fmt_delta(L, me, after)} |")
    out += ["", "_Gain = change in expected categories won per week against the other 5 teams (out of 45)._"]

    text = "\n".join(out)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"league_a_{date.today()}.md"
    path.write_text(text)
    print(text)
    print(f"\nSaved {path}")


if __name__ == "__main__":
    sys.exit(main())
