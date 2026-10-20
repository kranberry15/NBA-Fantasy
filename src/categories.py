"""League A (9-cat H2H) valuation: per-game z-scores, team category profiles, FA and trade search.

Per-game projections come from the same game-log model as League B (minutes, age, per-minute
rates). FG% and FT% are scored by volume impact: (player% - league%) x attempts.
TO is negative. Availability (projected games / 82) scales a player's weekly contribution.
"""
from __future__ import annotations

import itertools
import math

import numpy as np
import pandas as pd
import yaml

from .common import DATA, PROCESSED, norm_name

CATS = ["FG%", "FT%", "3PM", "REB", "AST", "STL", "BLK", "TO", "PTS"]
COUNT = {"3PM": "3pm_pg", "REB": "reb_pg", "AST": "ast_pg", "STL": "stl_pg", "BLK": "blk_pg",
         "TO": "to_pg", "PTS": "pts_pg"}
GAMES_PER_WEEK = 3.5


def load_players() -> pd.DataFrame:
    df = pd.read_csv(PROCESSED / "league_b_board.csv")  # league-neutral stat projections live here too
    df["avail"] = (df.g_proj / 82).clip(0, 1)
    # 2 IR slots + daily streaming cover most missed games, so injury *history* is softened;
    # players listed OUT right now keep their full penalty.
    out_now = df.inj.isin(["OUT", "INJURY_RESERVE", "SUSPENSION"])
    df["avail_eff"] = np.where(out_now, df.avail, np.maximum(df.avail, 0.85))
    return df


def load_rosters() -> dict:
    """Prefer the ESPN sync (espn_rosters.yaml, written by `league_a.py --sync`), else the screenshot file."""
    for fn in ("espn_rosters.yaml", "rosters.yaml"):
        path = DATA / "league_a_rosters" / fn
        if path.exists():
            r = yaml.safe_load(open(path))
            assert r["league"] == "league_a"
            return r
    raise FileNotFoundError("no League A rosters")


def add_zscores(df: pd.DataFrame, pool_size: int = 78) -> pd.DataFrame:
    """Per-game z-scores; mean/std from the top `pool_size` players (the rostered talent level)."""
    df = df.copy()
    # first pass on a rough value to pick the pool
    pool = df.sort_values("fp_total", ascending=False).head(pool_size)
    fg_lg = pool.fgm_pg.sum() / pool.fga_pg.sum()
    ft_lg = pool.ftm_pg.sum() / pool.fta_pg.sum()
    df["fg_impact"] = df.fgm_pg - fg_lg * df.fga_pg
    df["ft_impact"] = df.ftm_pg - ft_lg * df.fta_pg
    pool = df.loc[pool.index]
    cols = {"FG%": "fg_impact", "FT%": "ft_impact", **COUNT}
    for _ in range(2):  # re-pick pool by z-total and recompute
        for c, col in cols.items():
            mu, sd = pool[col].mean(), pool[col].std()
            if c in ("FG%", "FT%"):
                mu = 0.0
            z = (df[col] - mu) / sd
            df["z_" + c] = -z if c == "TO" else z
        df["z_total"] = df[[f"z_{c}" for c in CATS]].sum(axis=1)
        pool = df.sort_values("z_total", ascending=False).head(pool_size)
    df.attrs["fg_lg"], df.attrs["ft_lg"] = fg_lg, ft_lg
    return df


VEC = ["3pm_pg", "reb_pg", "ast_pg", "stl_pg", "blk_pg", "to_pg", "pts_pg", "fgm_pg", "fga_pg", "ftm_pg", "fta_pg"]


def contrib(players: pd.DataFrame) -> np.ndarray:
    """Weekly stat contributions per player (rows) in VEC order."""
    return players[VEC].fillna(0).to_numpy() * (players.avail_eff.to_numpy() * GAMES_PER_WEEK)[:, None]


def totals_from_vec(v: np.ndarray) -> dict:
    out = dict(zip(["3PM", "REB", "AST", "STL", "BLK", "TO", "PTS"], v[:7]))
    out["FG%"] = v[7] / max(v[8], 1e-9)
    out["FT%"] = v[9] / max(v[10], 1e-9)
    return out


def team_week(players: pd.DataFrame) -> dict:
    """Projected weekly category totals for a roster."""
    return totals_from_vec(contrib(players).sum(axis=0))


class LeagueA:
    def __init__(self, my_team: str = "Okc Champs 2027"):
        self.players = add_zscores(load_players())
        self.by_norm = {norm_name(n): i for i, n in self.players.name.items()}
        src = load_rosters()
        self.source = f"{src.get('source', 'screenshot')} ({src.get('as_of')})"
        self.unknown = [p for names in src["teams"].values() for p in names if norm_name(p) not in self.by_norm]
        self.rosters = {t: [self.by_norm[norm_name(p)] for p in names if norm_name(p) in self.by_norm]
                        for t, names in src["teams"].items()}
        self.me = next(t for t in self.rosters if norm_name(t) == norm_name(my_team))
        rostered = {i for ids in self.rosters.values() for i in ids}
        if src.get("free_agents"):  # ESPN's own list of who can be added
            ok = {self.by_norm[norm_name(n)] for n, _ in src["free_agents"] if norm_name(n) in self.by_norm}
            self.fa = self.players[self.players.index.isin(ok - rostered)]
        else:
            self.fa = self.players[~self.players.index.isin(rostered)]
        self.vec = pd.DataFrame(contrib(self.players), index=self.players.index)
        self.team_vec = {t: self.vec.loc[ids].to_numpy().sum(axis=0) for t, ids in self.rosters.items()}
        self.totals = {t: totals_from_vec(v) for t, v in self.team_vec.items()}
        tot = pd.DataFrame(self.totals).T
        self.cat_sd = {c: max(tot[c].std(), 1e-3 if "%" in c else 0.5) for c in CATS}

    def roster(self, team: str) -> pd.DataFrame:
        return self.players.loc[self.rosters[team]]

    def cat_table(self) -> pd.DataFrame:
        t = pd.DataFrame(self.totals).T[CATS]
        ranks = t.rank(ascending=False)
        ranks["TO"] = t["TO"].rank(ascending=True)  # fewer turnovers is better
        return t, ranks

    def score(self, totals: dict, team: str, cats=CATS) -> float:
        """Expected category wins per week vs every other team (internal ranking metric)."""
        s = 0.0
        for opp, ot in self.totals.items():
            if opp == team:
                continue
            for c in cats:
                d = (totals[c] - ot[c]) / (self.cat_sd[c] * 1.5)
                if c == "TO":
                    d = -d
                s += 0.5 * (1 + math.erf(d / math.sqrt(2)))
        return s

    def with_swap(self, team: str, out_ids, in_ids) -> dict:
        v = self.team_vec[team] - self.vec.loc[list(out_ids)].to_numpy().sum(axis=0) \
            + self.vec.loc[list(in_ids)].to_numpy().sum(axis=0)
        return totals_from_vec(v)

    def perceived(self, ids) -> float:
        """How the other manager likely values players: by ESPN draft position (name value)."""
        return float(sum(max(0.0, 150 - (a if a == a else 150)) for a in self.players.loc[list(ids)].adp))

    # ------------------------------------------------------------ waivers
    def fa_moves(self, team: str, n: int = 10, cats=CATS) -> pd.DataFrame:
        base = self.score(self.totals[team], team, cats)
        my = self.rosters[team]
        rows = []
        fa = self.fa.sort_values("z_total", ascending=False).head(120)
        for add in fa.index:
            for drop in my:
                s = self.score(self.with_swap(team, [drop], [add]), team, cats)
                rows.append({"add": add, "drop": drop, "gain": s - base})
        df = pd.DataFrame(rows).sort_values("gain", ascending=False)
        df = df[df.gain > 0].drop_duplicates("add").head(n)
        return df

    # ------------------------------------------------------------ trades
    def trades(self, team: str, n: int = 10, max_size: int = 2) -> pd.DataFrame:
        my_base = self.score(self.totals[team], team)
        rows = []
        my_ids = self.rosters[team]
        for opp, opp_ids in self.rosters.items():
            if opp == team:
                continue
            opp_base = self.score(self.totals[opp], opp)
            for k in range(1, max_size + 1):
                for give in itertools.combinations(my_ids, k):
                    for get in itertools.combinations(opp_ids, k):
                        zg = self.players.loc[list(give)].z_total.sum()
                        zr = self.players.loc[list(get)].z_total.sum()
                        if zr - zg > 2.5 or zg - zr > 4:  # they won't accept a lopsided z deal; skip absurd ones
                            continue
                        mine = self.score(self.with_swap(team, give, get), team)
                        if mine - my_base <= 0.15:
                            continue
                        # they must feel they win on name value (ESPN ADP), not just on our numbers
                        if self.perceived(give) < self.perceived(get) - 8:
                            continue
                        theirs = self.score(self.with_swap(opp, get, give), opp)
                        if theirs - opp_base <= 0.0:
                            continue
                        rows.append({"opp": opp, "give": give, "get": get, "my_gain": mine - my_base,
                                     "their_gain": theirs - opp_base, "z_give": zg, "z_get": zr})
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        df["rank_score"] = df.my_gain + 0.5 * df.their_gain
        return df.sort_values("rank_score", ascending=False).head(n)
