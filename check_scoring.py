#!/usr/bin/env python3
"""Compare config/league_b.yaml scoring with ESPN's live league settings. Exit 1 on any mismatch.

  python3 check_scoring.py
"""
from __future__ import annotations

import sys

import requests

from src.common import header, load_env, load_league
from src.espn_league import URL, _cookies
from src.projections import STAT_IDS


def main() -> int:
    cfg = load_league("league_b")
    print(header(cfg))
    d = requests.get(URL.format(season=cfg["season"], lid=cfg["league_id"]), params={"view": "mSettings"},
                     cookies=_cookies(load_env()), timeout=30).json()
    espn = {STAT_IDS.get(str(s["statId"]), f"stat{s['statId']}"): float(s["points"])
            for s in d["settings"]["scoringSettings"]["scoringItems"]}
    ours = {k: float(v) for k, v in cfg["scoring"].items()}
    bad = [(k, ours.get(k), espn.get(k)) for k in sorted(set(ours) | set(espn)) if ours.get(k) != espn.get(k)]
    print("| Stat | Config | ESPN |\n|---|---|---|")
    for k in sorted(set(ours) | set(espn)):
        print(f"| {k} | {ours.get(k, '-')} | {espn.get(k, '-')} |{' **MISMATCH**' if (k, ours.get(k), espn.get(k)) in bad else ''}")
    print("\nOK: config matches ESPN." if not bad else f"\n{len(bad)} mismatch(es). Fix config/league_b.yaml.")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
