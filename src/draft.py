"""Snake-draft engine for League B: state, recommendations, tier survival, roster tracking.

All work is on an in-memory table of ~650 players, so every command runs in milliseconds.
"""
from __future__ import annotations

import difflib
import json
import math

import numpy as np
import pandas as pd

from .common import DATA, PROCESSED, norm_name

REQUIRED = ["PG", "SG", "SF", "PF", "C", "G", "F"]  # starting slots that need a specific position
SLOT_OK = {"PG": {"PG"}, "SG": {"SG"}, "SF": {"SF"}, "PF": {"PF"}, "C": {"C"},
           "G": {"PG", "SG"}, "F": {"SF", "PF"}}


def snake_slot(pick: int, teams: int) -> int:
    """Draft slot (1..teams) that owns overall pick number `pick` (1-based)."""
    rnd, idx = divmod(pick - 1, teams)
    return idx + 1 if rnd % 2 == 0 else teams - idx


def my_pick_numbers(slot: int, teams: int, rounds: int) -> list[int]:
    return [n for n in range(1, teams * rounds + 1) if snake_slot(n, teams) == slot]


def max_matching(pos_lists: list[list[str]]) -> int:
    """How many of the 7 position-specific starting slots this roster can fill (bipartite matching)."""
    match: dict[str, int] = {}

    def try_assign(i, seen):
        for slot in REQUIRED:
            if slot in seen or not (set(pos_lists[i]) & SLOT_OK[slot]):
                continue
            seen.add(slot)
            if slot not in match or try_assign(match[slot], seen):
                match[slot] = i
                return True
        return False

    return sum(try_assign(i, set()) for i in range(len(pos_lists)))


class Draft:
    def __init__(self, cfg: dict, board: pd.DataFrame | None = None, state_path=None, fresh=False):
        self.cfg = cfg
        self.teams = cfg["teams"]
        self.rounds = cfg["draft"]["rounds"]
        self.max_c = cfg["roster"]["max_centers"]
        self.board = board if board is not None else pd.read_csv(PROCESSED / f"{cfg['key']}_board.csv")
        self.board = self.board.set_index("id", drop=False)
        self.board["pos_list"] = self.board.pos.str.split("/")
        self.board["opp_rank"] = self._opponent_rank()
        self._norm = {pid: norm_name(n) for pid, n in self.board.name.items()}
        self.state_path = state_path or DATA / f"draft_state_{cfg['key']}.json"
        self.picks: list[dict] = []  # {n, slot, id, name}
        self.my_slot = cfg["draft"].get("my_pick")
        self.team_names: dict[int, str] = {}
        if not fresh and self.state_path.exists():
            st = json.loads(self.state_path.read_text())
            if st.get("league") == cfg["key"]:
                self.picks = st["picks"]
                self.my_slot = st.get("my_slot") or self.my_slot
                self.team_names = {int(k): v for k, v in st.get("team_names", {}).items()}

    # ------------------------------------------------------------ state
    def _opponent_rank(self) -> pd.Series:
        """How opponents will likely draft: mix of ESPN ADP and ESPN's points rank (shown in their draft room)."""
        adp = self.board.adp.fillna(200)
        er = self.board.espn_rank.fillna(200)
        return (0.5 * adp + 0.5 * er)

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"league": self.cfg["key"], "my_slot": self.my_slot,
                                               "picks": self.picks, "team_names": self.team_names}, indent=1))

    @property
    def current(self) -> int:
        return len(self.picks) + 1

    @property
    def done(self) -> bool:
        return self.current > self.teams * self.rounds

    def taken_ids(self) -> set:
        return {p["id"] for p in self.picks}

    def available(self) -> pd.DataFrame:
        return self.board[~self.board.index.isin(self.taken_ids())]

    def my_picks(self) -> list[int]:
        return my_pick_numbers(self.my_slot, self.teams, self.rounds) if self.my_slot else []

    def my_roster(self) -> pd.DataFrame:
        ids = [p["id"] for p in self.picks if p["slot"] == self.my_slot]
        return self.board.loc[[i for i in ids if i in self.board.index]]

    def next_my_pick(self, after: int | None = None) -> int | None:
        after = self.current if after is None else after
        return next((n for n in self.my_picks() if n >= after), None)

    def set_slot(self, slot: int):
        if not 1 <= slot <= self.teams:
            raise ValueError(f"slot must be 1..{self.teams}")
        self.my_slot = slot
        self.save()

    # ------------------------------------------------------------ lookup
    def find(self, text: str, pool: pd.DataFrame | None = None) -> list[int]:
        pool = self.available() if pool is None else pool
        text = text.strip()
        if text.startswith("#") and text[1:].isdigit():
            pid = int(text[1:])
            return [pid] if pid in pool.index else []
        q = norm_name(text)
        if not q:
            return []
        names = {pid: self._norm[pid] for pid in pool.index}
        exact = [pid for pid, n in names.items() if n == q]
        if exact:
            return exact
        parts = q.split()
        contains = [pid for pid, n in names.items() if all(t in n.split() or n.startswith(q) for t in parts)]
        if not contains:
            contains = [pid for pid, n in names.items() if q in n]
        if contains:
            return sorted(contains, key=lambda i: pool.at[i, "rank"])
        close = difflib.get_close_matches(q, list(names.values()), n=5, cutoff=0.6)
        return [pid for c in close for pid, n in names.items() if n == c]

    def record(self, pid: int) -> dict:
        if self.done:
            raise ValueError("draft is complete")
        if pid in self.taken_ids():
            raise ValueError(f"{self.board.at[pid, 'name']} is already drafted")
        n = self.current
        pick = {"n": n, "slot": snake_slot(n, self.teams), "id": int(pid),
                "name": str(self.board.at[pid, "name"]) if pid in self.board.index else f"ESPN#{pid}"}
        self.picks.append(pick)
        self.save()
        return pick

    def undo(self) -> dict | None:
        if not self.picks:
            return None
        p = self.picks.pop()
        self.save()
        return p

    # ------------------------------------------------------------ roster rules
    def center_counts(self, roster: pd.DataFrame | None = None) -> tuple[int, int]:
        r = self.my_roster() if roster is None else roster
        return int((r.primary == "C").sum()), int(r.c_elig.sum())

    def roster_fill_status(self, roster: pd.DataFrame | None = None) -> tuple[int, list[str]]:
        r = self.my_roster() if roster is None else roster
        lists = list(r.pos_list)
        filled = max_matching(lists)
        missing = [s for s in REQUIRED if max_matching(lists + [list(SLOT_OK[s])]) > filled]
        return filled, missing

    def eligible_for_me(self, avail: pd.DataFrame) -> pd.DataFrame:
        """Remove players I can't/shouldn't take: 4th center, or non-fillers when slots are at risk."""
        roster = self.my_roster()
        out = avail
        prim_c, _ = self.center_counts(roster)
        if self.max_c and prim_c >= self.max_c:
            out = out[out.primary != "C"]
        picks_left = sum(1 for n in self.my_picks() if n >= self.current)
        filled, missing = self.roster_fill_status(roster)
        need = len(REQUIRED) - filled
        if need and picks_left <= need:
            lists = list(roster.pos_list)
            out = out[out.pos_list.apply(lambda pl: max_matching(lists + [pl]) > filled)]
        return out

    # ------------------------------------------------------------ survival model
    def survival(self, avail: pd.DataFrame, picks_between: int) -> pd.Series:
        """P(player still available after `picks_between` opponent picks).

        Opponents are assumed to draft roughly in ESPN order (ADP + ESPN points rank). A player whose
        rank among the remaining players is r goes at pick ~N(r, 0.3 + 0.2r): top names rarely slide,
        later names vary more.
        """
        if picks_between <= 0:
            return pd.Series(1.0, index=avail.index)
        r = avail.opp_rank.rank(method="first")
        z = (r - picks_between - 0.5) / (0.3 + 0.2 * r)
        return z.apply(lambda v: 0.5 * (1 + math.erf(v / math.sqrt(2))))

    def expected_best(self, pool: pd.DataFrame, surv: pd.Series) -> float:
        """E[best VORP I can take at my next pick] given independent survival probabilities."""
        p_none = 1.0
        ev = 0.0
        for pid in pool.sort_values("vorp", ascending=False).index[:40]:
            ps = surv[pid]
            ev += p_none * ps * pool.at[pid, "vorp"]
            p_none *= 1 - ps
        return ev

    # ------------------------------------------------------------ recommendation
    def recommend(self, k: int = 3) -> dict:
        avail = self.available()
        mine = self.eligible_for_me(avail)
        nxt = self.next_my_pick(self.current + 1) if self.my_slot else None
        between = (nxt - self.current - 1) if nxt else 0
        cands = mine.sort_values("vorp", ascending=False).head(8)
        rows = []
        surv_all = self.survival(avail, between)
        for pid in cands.index:
            rest = avail.drop(index=pid)
            if nxt:
                surv = self.survival(rest, between)
                pool = rest
                if self.max_c and self.center_counts()[0] + (cands.at[pid, "primary"] == "C") >= self.max_c:
                    pool = rest[rest.primary != "C"]
                follow = self.expected_best(pool, surv)
            else:
                follow = 0.0
            rows.append({"id": pid, "vorp": cands.at[pid, "vorp"], "follow": follow,
                         "plan": cands.at[pid, "vorp"] + follow, "survive": float(surv_all[pid])})
        plan = pd.DataFrame(rows).sort_values("plan", ascending=False) if rows else pd.DataFrame()
        return {"plan": plan.head(k), "next_pick": nxt, "between": between, "surv": surv_all}

    def reason(self, pid: int, survive: float | None = None, nxt: int | None = None) -> str:
        r = self.board.loc[pid]
        bits = [f"{r.fppg:.1f} FP/G x {r.g_proj:.0f} G = {r.fp_total:.0f} pts, VORP {r.vorp:.0f} (our #{r['rank']})"]
        strengths = []
        stocks = 4 * (r.stl_pg + r.blk_pg)
        if stocks >= 8:
            strengths.append(f"stocks worth {stocks:.1f}/g")
        if r.bonus_pg >= 1.5:
            strengths.append(f"DD/TD bonus {r.bonus_pg:.1f}/g ({r.dd_pg:.0%} DD rate)")
        if r.ast_pg >= 6:
            strengths.append(f"{r.ast_pg:.1f} AST (={2.5 * r.ast_pg:.0f} pts)")
        if r.fg_pct >= 0.55:
            strengths.append(f"efficient {r.fg_pct:.0%} FG")
        if r.ft_miss_cost_pg >= 3:
            strengths.append(f"FT misses cost {r.ft_miss_cost_pg:.1f}/g")
        if r.tov_cost_pg >= 7:
            strengths.append(f"TOs cost {r.tov_cost_pg:.1f}/g")
        if strengths:
            bits.append(", ".join(strengths))
        if not math.isnan(r.adp) and r.adp >= 135:
            bits.append("undrafted in most ESPN leagues (sleeper)")
        elif not math.isnan(r.adp):
            e = r.edge
            tag = f"ADP {r.adp:.0f}" + (f" (we rank him {e:+.0f} spots higher)" if e and e >= 5 else
                                        f" (market overrates by {-e:.0f})" if e and e <= -5 else "")
            bits.append(tag)
        if survive is not None and nxt:
            bits.append(f"~{survive:.0%} chance he lasts to your pick {nxt}")
        warn = [w for w in (r.inj if isinstance(r.inj, str) else "", r.flags if isinstance(r.flags, str) else "") if w]
        if warn:
            bits.append("FLAGS: " + " ".join(warn))
        return " | ".join(bits)

    def tier_outlook(self, after_taking: int | None = None, max_tiers: int = 8) -> pd.DataFrame:
        avail = self.available()
        if after_taking is not None and after_taking in avail.index:
            avail = avail.drop(index=after_taking)
        nxt = self.next_my_pick(self.current + 1) if self.my_slot else None
        between = (nxt - self.current - 1) if nxt else 0
        surv = self.survival(avail, between)
        rows = []
        for t, g in avail.groupby("tier"):
            rows.append({"tier": t, "left_now": len(g), "exp_left_at_next": round(float(surv[g.index].sum()), 1),
                         "best": g.sort_values("vorp", ascending=False).name.iloc[0],
                         "vorp_range": f"{g.vorp.max():.0f}-{g.vorp.min():.0f}"})
            if len(rows) >= max_tiers:
                break
        out = pd.DataFrame(rows)
        if len(out) > 1:
            tops = avail.groupby("tier").vorp.max()
            out["drop_to_next_tier"] = [round(float(tops.get(t, 0) - tops.get(t + 1, 0)), 0) for t in out.tier]
        return out
