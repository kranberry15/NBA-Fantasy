#!/usr/bin/env python3
"""Show every non-draft move in both leagues from ESPN's transaction log, including
CANCELED waiver claims that the ESPN app does not display. Other teams' PENDING claims
are hidden by ESPN until they process.

  python3 activity.py            # both leagues
  python3 activity.py league_a   # one league
"""
from __future__ import annotations

import datetime as dt
import json
import sys

import requests

from src.common import header, load_env, load_league
from src.espn_league import URL, _cookies

HK = dt.timezone(dt.timedelta(hours=8))
SKIP = {"DRAFT", "ROSTER", "FUTURE_ROSTER"}


def activity(key: str) -> None:
    cfg = load_league(key)
    url = URL.format(season=cfg["season"], lid=cfg["league_id"])
    ck = _cookies(load_env())
    d = requests.get(url, params=[("view", "mTransactions2"), ("view", "mPendingTransactions"), ("view", "mTeam")],
                     cookies=ck, timeout=30).json()
    teams = {t["id"]: t["name"].strip() for t in d["teams"]}
    tx = [x for x in d.get("transactions", []) if x["type"] not in SKIP]
    ids = list({i["playerId"] for x in tx for i in x.get("items", [])})
    names = {}
    if ids:
        flt = {"players": {"filterIds": {"value": ids}}}
        r = requests.get(url, params={"view": "kona_player_info"}, headers={"X-Fantasy-Filter": json.dumps(flt)},
                         cookies=ck, timeout=30).json()
        names = {p["id"]: p["player"]["fullName"] for p in r["players"]}
    print(header(cfg))
    if not tx:
        print("  no moves yet")
    for x in sorted(tx, key=lambda x: x["proposedDate"]):
        when = dt.datetime.fromtimestamp(x["proposedDate"] / 1000, HK).strftime("%a %d %b %H:%M HKT")
        bid = f" ${x['bidAmount']}" if x.get("bidAmount") else ""
        items = ", ".join(f"{i['type']} {names.get(i['playerId'], i['playerId'])}" for i in x.get("items", []))
        print(f"  {when} | {teams.get(x.get('teamId'), '-'):<28} | {x['type']} {x['status']}{bid} | {items}")
    print()


if __name__ == "__main__":
    for key in (sys.argv[1:] or ["league_a", "league_b"]):
        activity(key)
