#!/usr/bin/env python3
"""League A (9-cat) report: category profile, waiver pickups, trade ideas. Recommend-only.

  python league_a.py            # writes reports/league_a_<date>.md and prints it
"""
from __future__ import annotations

import sys
from datetime import date

import pandas as pd

from src.categories import CATS, LeagueA
from src.common import DATA, REPORTS, header, load_league


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


def sync(cfg) -> None:
    import yaml
    from src.common import load_env
    from src.espn_league import fetch_rosters
    try:
        d = fetch_rosters(cfg, load_env())
    except Exception as e:
        print(f"[WARN] ESPN sync failed ({e}). Using the last saved rosters.")
        return
    out = {"league": "league_a", "source": "ESPN", "as_of": str(date.today()), "teams": d["teams"],
           "faab_spent": d["faab_spent"], "free_agents": [list(x) for x in d["free_agents"]]}
    path = DATA / "league_a_rosters" / "espn_rosters.yaml"
    path.write_text(yaml.safe_dump(out, allow_unicode=True, sort_keys=False))
    print(f"Synced {sum(map(len, d['teams'].values()))} rostered players, {len(d['free_agents'])} free agents/waivers")


def evaluate(cfg, opp_name: str, give: list[str], get: list[str]) -> None:
    """Score one trade: python league_a.py --eval "Toronto" "Jalen Williams,Jaylen Brown" "Kevin Durant,Mikal Bridges" """
    from src.common import norm_name
    L = LeagueA(cfg["my_team_name"])
    P = L.players
    opp = next(t for t in L.rosters if norm_name(opp_name) in norm_name(t))

    def ids(names, team):
        out = []
        for n in names:
            m = [i for i in L.rosters[team] if norm_name(n) in norm_name(P.at[i, "name"])]
            if not m:
                raise SystemExit(f"'{n}' is not on {team}'s roster")
            out.append(m[0])
        return out
    g, q = ids(give, L.me), ids(get, opp)
    extra = len(g) - len(q)
    q_me, g_opp_drop = list(q), []
    if extra > 0:
        # I fill open spots with the free agents that help me most; they cut their weakest players
        for _ in range(extra):
            best = max(L.fa.sort_values("z_total", ascending=False).index[:60],
                       key=lambda f: L.score(L.with_swap(L.me, g, q_me + [f]), L.me) if f not in q_me else -1)
            q_me.append(best)
        keep = [i for i in L.rosters[opp] if i not in q]
        g_opp_drop = list(P.loc[keep].z_total.nsmallest(extra).index)
        print(f"(you add free agent {', '.join(P.at[i, 'name'] for i in q_me[len(q):])}; "
              f"{opp} must cut {', '.join(P.at[i, 'name'] for i in g_opp_drop)})")
    T = dict(L.totals)
    T[L.me], T[opp] = L.with_swap(L.me, g, q_me), L.with_swap(opp, q + g_opp_drop, g)
    before = {t: L.score(L.totals[t], t) / 5 for t in L.totals}
    old, L.totals = L.totals, T
    after = {t: L.score(T[t], t) / 5 for t in T}
    L.totals = old
    print(header(cfg))
    print(f"GIVE {', '.join(P.at[i, 'name'] for i in g)}  |  GET {', '.join(P.at[i, 'name'] for i in q)}  ({opp})")
    print(f"You:  {before[L.me]:.2f} -> {after[L.me]:.2f} cat-wins/wk ({after[L.me] - before[L.me]:+.2f})")
    print(f"Them: {before[opp]:.2f} -> {after[opp]:.2f} ({after[opp] - before[opp]:+.2f})")
    order = sorted(after, key=lambda t: -after[t])
    print("New order: " + " > ".join(f"{t} {after[t]:.2f}" for t in order))
    print("Your categories: " + fmt_delta(L, L.me, T[L.me]))
    verdict = "ACCEPT" if after[L.me] - before[L.me] > 0.15 else "DECLINE (gain too small or negative)"
    print(f"Verdict: {verdict}")


def main():
    cfg = load_league("league_a")
    if "--eval" in sys.argv:
        i = sys.argv.index("--eval")
        opp, give, get = sys.argv[i + 1:i + 4]
        return evaluate(cfg, opp, [x.strip() for x in give.split(",")], [x.strip() for x in get.split(",")])
    if "--sync" in sys.argv:
        sync(cfg)
    L = LeagueA(cfg["my_team_name"])
    P = L.players
    me = L.me
    out = [f"# {header(cfg)}", f"_Report {date.today()} - rosters from {L.source} - recommend only, "
           f"nothing is done without your OK._", ""]
    if L.unknown:
        out += [f"Players with no stats in our data (ignored): {', '.join(L.unknown)}", ""]

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
