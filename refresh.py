#!/usr/bin/env python3
"""Pull fresh data and rebuild player values.

  python refresh.py            # use cache if < 12h old
  python refresh.py --force    # re-download everything from ESPN

Sources: ESPN public fantasy feed (game logs, injuries, positions, ADP),
Basketball Reference (ages, cached 30 days). nba_api (stats.nba.com) is tried
only if you pass --nba-api; it is often blocked from cloud servers.
"""
from __future__ import annotations

import argparse
import sys
import time

from src import bbref, espn_public, projections
from src.common import load_league, header


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--league", default="league_b", choices=["league_b"])
    args = ap.parse_args(argv)
    cfg = load_league(args.league)
    t0 = time.time()
    season = cfg["season"]
    seasons = sorted(int(s) for s in cfg["projection"]["season_weights"])
    print(header(cfg))
    current = espn_public.fetch_players(season, limit=1000, force=args.force)
    by_season = {season: current}
    for s in seasons:
        # completed seasons rarely change: cache for 30 days
        by_season[s] = espn_public.fetch_players(s, limit=750, force=args.force, max_age_hours=24 * 30)
    print(f"ESPN: {len(current)} current players; seasons {seasons} loaded")
    logs = projections.parse_logs(by_season)
    projections.save_logs_sqlite(logs)
    print(f"Game logs: {len(logs):,} player-games")
    ages = bbref.ages(season)
    print(f"Ages: {len(ages)} players (Basketball Reference)")
    df = projections.build_values(cfg, current, logs, ages)
    path = projections.save_board(df, cfg)
    print(f"Board: {len(df)} players -> {path}  ({time.time() - t0:.1f}s)")
    print("Replacement level (season FP):", {k: round(v) for k, v in df.attrs['replacement'].items()})


if __name__ == "__main__":
    sys.exit(main())
