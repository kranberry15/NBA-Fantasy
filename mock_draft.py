#!/usr/bin/env python3
"""Mock draft: 9 bots draft by ESPN ADP/rank (with random noise); you draft with the tool's PICK.

  python mock_draft.py --slot 4 --seed 1
Checks speed (< 2 s per recommendation), the 3-center cap and that every starting slot can be filled.
Does not touch your real draft state.
"""
from __future__ import annotations

import argparse
import time

import numpy as np

from src.common import DATA, header, load_league
from src.draft import Draft, snake_slot


def run(slot: int, seed: int, verbose: bool = True) -> dict:
    cfg = load_league("league_b")
    d = Draft(cfg, state_path=DATA / "draft_state_mock.json", fresh=True)
    d.set_slot(slot)
    rng = np.random.default_rng(seed)
    times = []
    while not d.done:
        avail = d.available()
        if snake_slot(d.current, d.teams) == slot:
            t0 = time.time()
            rec = d.recommend()
            times.append(time.time() - t0)
            pid = int(rec["plan"].iloc[0].id)
            if verbose:
                r = d.board.loc[pid]
                print(f"pick {d.current:>3}: {r['name']:<26} {r.pos:<6} FP/G {r.fppg:5.1f}  VORP {r.vorp:6.0f}  "
                      f"ADP {r.adp if r.adp == r.adp else '-'}")
        else:  # bot: ESPN order + noise, max 3 primary C
            noisy = avail.opp_rank * np.exp(rng.normal(0, 0.2, len(avail))) + rng.normal(0, 1.0, len(avail))
            team_ids = [p["id"] for p in d.picks if p["slot"] == snake_slot(d.current, d.teams)]
            if (d.board.loc[team_ids].primary == "C").sum() >= 3:
                noisy = noisy[avail.primary != "C"]
            pid = int(noisy.idxmin())
        d.record(pid)
    totals = {}
    for s in range(1, d.teams + 1):
        ids = [p["id"] for p in d.picks if p["slot"] == s]
        totals[s] = d.board.loc[ids].fp_total.sum()
    mine = d.my_roster()
    filled, missing = d.roster_fill_status()
    prim_c, _ = d.center_counts()
    res = {"max_ms": 1000 * max(times), "avg_ms": 1000 * np.mean(times), "centers": prim_c,
           "slots_filled": filled, "missing": missing, "roster": len(mine),
           "my_total": totals[slot], "rank": sorted(totals.values(), reverse=True).index(totals[slot]) + 1,
           "others_avg": np.mean([v for k, v in totals.items() if k != slot])}
    d.state_path.unlink(missing_ok=True)
    return res


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", type=int, default=4)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    print(header(load_league("league_b")))
    r = run(a.slot, a.seed)
    print(f"\nRoster {r['roster']}/13 | primary C {r['centers']}/3 | required slots filled {r['slots_filled']}/7 "
          f"{r['missing'] or ''}")
    print(f"Projected season points: me {r['my_total']:.0f} vs other teams avg {r['others_avg']:.0f} "
          f"(rank {r['rank']}/10)")
    print(f"Recommendation speed: avg {r['avg_ms']:.0f} ms, max {r['max_ms']:.0f} ms")
