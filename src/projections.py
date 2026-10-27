"""Build League B (points) player values from ESPN game logs.

Method (all numbers from real game logs, no ESPN projections except for rookies):
  1. Per-minute rates of every scoring stat, blended across 3 seasons (recent weighted more),
     regressed toward a bench-player baseline when the minute sample is small.
  2. Projected minutes = season-blended MPG mixed with the last 20 games, adjusted for age.
  3. DD / TD / QD bonus rates counted directly from game logs (ESPN's own flags).
  4. Projected games = games-played history (regressed) minus current injury / suspension.
  5. Season value = FP/G x games. VORP vs replacement at each position after 130 picks.
"""
from __future__ import annotations

import sqlite3
from collections import defaultdict

import numpy as np
import pandas as pd

from .common import DATA, DEFAULT_POS, PRO_TEAM, PROCESSED, SLOT_POS, norm_name


_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "ten": 10, "twelve": 12}


def _games_from_news(text: str, default: float) -> tuple[float, str]:
    """Games missed from ESPN injury wording; ~3.5 NBA games per week. Falls back to the config default."""
    import re
    w = r"(\d+|" + "|".join(_NUM) + r")"
    m = re.search(w + r"(?:\s*(?:-to-|-|to)\s*" + w + r")?\s*(?:-|\s)?weeks?", text)
    if m:
        lo = _NUM.get(m.group(1), None) or int(m.group(1))
        hi = (_NUM.get(m.group(2), None) or int(m.group(2))) if m.group(2) else lo
        return 3.5 * (lo + hi) / 2, f"ESPN timeline {m.group(0)}"
    m = re.search(r"(?:first|next)\s+" + w + r"(?:\s+or\s+" + w + r")?\s+games", text)
    if m:
        lo = _NUM.get(m.group(1), None) or int(m.group(1))
        return float(lo), f"ESPN timeline {m.group(0)}"
    if any(k in text for k in ("season-ending", "out for the season", "torn achilles", "acl")):
        return default * 3, "long-term per ESPN news"
    if "mild" in text:
        return 4.0, "ESPN: mild"
    if re.search(r"ruled out for \w+'s game", text) and "extended" not in text:
        return 5.0, "ESPN: single-game ruling, no timeline"
    return default, "no timeline in news; config default"


def _news(pid):
    try:
        from .gamecontext import news
        return news(int(pid))
    except Exception:
        return []

# ESPN stat id -> our name
STAT_IDS = {"0": "PTS", "1": "BLK", "2": "STL", "3": "AST", "4": "OREB", "6": "REB", "10": "TF",
            "11": "TO", "13": "FGM", "14": "FGA", "15": "FTM", "16": "FTA", "17": "3PM", "18": "3PA",
            "24": "FTMI", "25": "3PMI", "37": "DD", "38": "TD", "39": "QD", "40": "MIN",
            # game context (not scored): fouls, ejections, flagrants, fouled out, started
            "9": "PF", "7": "EJ", "8": "FF", "12": "DQ", "41": "GS"}
RATE_STATS = ["PTS", "FGM", "FGA", "FTM", "FTA", "FTMI", "3PM", "3PMI", "OREB", "REB", "AST",
              "STL", "BLK", "TO", "TF"]
BONUS_STATS = ["DD", "TD", "QD"]
K_MINUTES = 250       # regression strength for per-minute rates (minutes)
K_GAMES_BONUS = 15    # regression strength for DD/TD rates (games)


# ---------------------------------------------------------------- game logs
def parse_logs(players_by_season: dict[int, list[dict]]) -> pd.DataFrame:
    rows = {}
    for season, players in players_by_season.items():
        for rec in players:
            p = rec["player"]
            for s in p.get("stats", []):
                if s.get("statSplitTypeId") != 5 or s.get("seasonId") != season:
                    continue
                st = s.get("stats") or {}
                if not st or st.get("40", 0) <= 0:
                    continue
                key = (p["id"], s.get("externalId") or s["id"])
                row = {"player_id": p["id"], "season": season, "game_id": s.get("externalId"),
                       "period": s.get("scoringPeriodId"), "team_id": s.get("proTeamId")}
                for sid, name in STAT_IDS.items():
                    row[name] = float(st.get(sid, 0.0))
                rows[key] = row
    df = pd.DataFrame(rows.values())
    return df.sort_values(["player_id", "season", "period"]).reset_index(drop=True)


def save_logs_sqlite(logs: pd.DataFrame) -> None:
    with sqlite3.connect(DATA / "cache.sqlite") as con:
        logs.to_sql("game_logs", con, if_exists="replace", index=False)


def fantasy_points(df: pd.DataFrame, weights: dict) -> pd.Series:
    return sum(df[k] * float(w) for k, w in weights.items() if k in df)


# ---------------------------------------------------------------- helpers
def age_factor(age: float | None) -> float:
    if age is None or np.isnan(age):
        return 1.0
    # per-minute production curve: young players improve, 31+ decline gently
    table = {21: 1.04, 22: 1.03, 23: 1.02, 24: 1.01, 31: 0.99, 32: 0.985, 33: 0.975, 34: 0.965,
             35: 0.955}
    a = int(age)
    if a <= 21:
        return 1.04
    if a >= 36:
        return 0.94
    return table.get(a, 1.0)


def minutes_age_adj(age: float | None) -> float:
    if age is None or np.isnan(age):
        return 0.0
    if age >= 33:
        return -min(0.5 * (age - 32), 2.5)
    if age <= 23:
        return 0.5 * (24 - age)
    return 0.0


# ---------------------------------------------------------------- main build
def build_values(cfg: dict, current: list[dict], logs: pd.DataFrame, ages: dict[str, float]) -> pd.DataFrame:
    W = cfg["scoring"]
    pcfg = cfg["projection"]
    sw = {int(k): float(v) for k, v in pcfg["season_weights"].items()}
    team_games = pcfg["team_games"]
    ov = cfg.get("overrides") or {}
    ov_games = {norm_name(k): v for k, v in (ov.get("games_missed") or {}).items()}
    ov_min = {norm_name(k): v for k, v in (ov.get("minutes") or {}).items()}
    ov_susp = {norm_name(k): v for k, v in (ov.get("suspended") or {}).items()}

    # The season in progress joins the blend. Its weight is per minute played, so 2 games barely move a
    # projection while 30 games dominate it. Games-played history still uses completed seasons only.
    cur = int(cfg["season"])
    if (logs["season"] == cur).any():
        sw_rates = {**sw, cur: float(pcfg.get("current_season_weight", 1.0))}
    else:
        sw_rates = dict(sw)
    logs = logs[logs["season"].isin(sw_rates)].copy()
    logs["FP"] = fantasy_points(logs, W)

    # season aggregates
    agg = logs.groupby(["player_id", "season"]).agg(
        GP=("MIN", "size"), **{c: (c, "sum") for c in RATE_STATS + BONUS_STATS + ["MIN", "FP"]})
    agg = agg.reset_index()
    by_player = {pid: g.set_index("season") for pid, g in agg.groupby("player_id")}

    # baseline per-minute rates: bench-level players (500-1200 min) in most recent season
    last_season = max(sw)
    a_last = agg[(agg.season == last_season) & agg.MIN.between(500, 1200)]
    prior = {c: a_last[c].sum() / a_last.MIN.sum() for c in RATE_STATS}

    # DD-rate model for rookies: DD/G vs second-best of PTS/REB/AST per game (veterans, last season)
    a_vet = agg[(agg.season == last_season) & (agg.GP >= 30)].copy()
    for c in ("PTS", "REB", "AST"):
        a_vet[c + "pg"] = a_vet[c] / a_vet.GP
    a_vet["second"] = np.sort(a_vet[["PTSpg", "REBpg", "ASTpg"]].values, axis=1)[:, 1]
    a_vet["ddpg"] = a_vet.DD / a_vet.GP
    bins = np.arange(0, 14, 1.0)
    a_vet["bin"] = np.digitize(a_vet.second, bins)
    dd_curve = a_vet.groupby("bin").agg(x=("second", "mean"), y=("ddpg", "mean")).sort_values("x")

    # recent minutes (last 20 games of the latest season each player played)
    recent = (logs.sort_values(["player_id", "season", "period"]).groupby("player_id").tail(20)
              .groupby("player_id").agg(rec_mpg=("MIN", "mean"), rec_n=("MIN", "size")))
    last_team = logs.sort_values(["season", "period"]).groupby("player_id")["team_id"].last()
    first_season = logs.groupby("player_id")["season"].min()

    rows = []
    for rec in current:
        p = rec["player"]
        pid = p["id"]
        name = p["fullName"]
        nn = norm_name(name)
        pos = [SLOT_POS[s] for s in sorted(p.get("eligibleSlots", [])) if s in SLOT_POS]
        if not pos:
            continue
        age = ages.get(nn, np.nan)
        team = PRO_TEAM.get(p.get("proTeamId", 0), "FA")
        inj = p.get("injuryStatus") or "ACTIVE"
        flags, notes = [], []
        own = p.get("ownership") or {}
        adp = own.get("averageDraftPosition") or np.nan
        # ESPN "STANDARD" rank = the points-league ranking opponents see in the draft room
        espn_rank = ((p.get("draftRanksByRankType") or {}).get("STANDARD") or {}).get("rank") or np.nan

        hist = by_player.get(pid)
        if hist is not None and hist.MIN.sum() > 0:
            source = "logs"
            seasons = [s for s in sw_rates if s in hist.index]
            wsum = sum(sw_rates[s] for s in seasons)
            wt = {s: sw_rates[s] / wsum for s in seasons}
            M = hist.MIN.sum()
            # rookies (only this season's games): shrink toward ESPN's stat line, not a bench player
            p_prior, k_min = prior, K_MINUTES
            if seasons == [cur]:
                esp = next((x.get("averageStats") for x in p.get("stats", []) if x.get("statSourceId") == 1
                            and x.get("seasonId") == cur and x.get("averageStats")), None)
                esp_min = float(esp.get("40", esp.get("28", 0))) if esp else 0
                if esp_min > 0:
                    p_prior = {c: float(esp.get(sid, 0.0)) / esp_min for sid, c in STAT_IDS.items() if c in RATE_STATS}
                    p_prior["OREB"] = p_prior.get("OREB") or 0.25 * p_prior.get("REB", 0)
                    p_prior["TF"] = p_prior.get("TF") or prior["TF"]
                    p_prior = {c: v * (0.9 if W.get(c, 0) > 0 else 1.0) for c, v in p_prior.items()}  # same -10% as preseason
                    k_min = 600
            rates = {}
            for c in RATE_STATS:
                obs = sum(wt[s] * hist.at[s, c] for s in seasons) / sum(wt[s] * hist.at[s, "MIN"] for s in seasons)
                rates[c] = (obs * M + p_prior.get(c, prior[c]) * k_min) / (M + k_min)
            gp_w = sum(wt[s] * hist.at[s, "GP"] for s in seasons)
            mpg_hist = sum(wt[s] * hist.at[s, "MIN"] for s in seasons) / gp_w
            G = hist.GP.sum()
            bonus = {}
            for c in BONUS_STATS:
                obs = sum(wt[s] * hist.at[s, c] for s in seasons) / gp_w
                bonus[c] = obs * G / (G + K_GAMES_BONUS)
            rmpg = recent.rec_mpg.get(pid, mpg_hist)
            if seasons == [cur] and k_min == 600:  # rookie: minutes also shrink toward ESPN's line (10 games)
                g_now = hist.at[cur, "GP"]
                mpg_hist = (hist.at[cur, "MIN"] + esp_min * 10) / (g_now + 10)
                rmpg = mpg_hist
            mpg = 0.75 * mpg_hist + 0.25 * rmpg
            if abs(rmpg - mpg_hist) >= 4:
                flags.append("MIN↑" if rmpg > mpg_hist else "MIN↓")
                notes.append(f"last-20 MPG {rmpg:.1f} vs {mpg_hist:.1f}")
            # usage proxy trend: (FGA + .44 FTA + TO) per 36, last season vs prior season
            if last_season in hist.index and (last_season - 1) in hist.index:
                def usg(s):
                    h = hist.loc[s]
                    return 36 * (h.FGA + 0.44 * h.FTA + h.TO) / h.MIN
                du = usg(last_season) - usg(last_season - 1)
                if abs(du) >= 2.5 and hist.at[last_season, "MIN"] > 500:
                    flags.append("USG↑" if du > 0 else "USG↓")
            # availability (games played rate)
            debut = first_season.get(pid, min(sw))
            num = den = 0.0
            for s in sw:
                if s < debut:
                    continue
                gp = hist.at[s, "GP"] if s in hist.index else 0
                w = sw[s] * (0.5 if gp == 0 else 1.0)  # a fully missed season counts half
                num += w * gp / team_games
                den += w
            r = float(pcfg.get("games_regression_seasons", 1.0)) * 0.35
            avail = (num + r * 0.78) / (den + r)
            g_proj = min(team_games * avail, 80)
            if M < 600:
                flags.append("SMALL-SAMPLE")
            lt = last_team.get(pid)
            if p.get("proTeamId", 0) and lt and lt != p.get("proTeamId"):
                flags.append("NEW-TEAM")
                notes.append(f"was {PRO_TEAM.get(lt, '?')}")
            cur_gp = int(hist.at[cur, "GP"]) if cur in hist.index else 0
            cur_fppg = (hist.at[cur, "FP"] / cur_gp) if cur_gp else np.nan
            last_fppg = (hist.at[last_season, "FP"] / hist.at[last_season, "GP"]) if last_season in hist.index else np.nan
            last_gp = int(hist.at[last_season, "GP"]) if last_season in hist.index else 0
        else:
            proj = next((s.get("averageStats") for s in p.get("stats", [])
                         if s.get("statSourceId") == 1 and s.get("seasonId") == cfg["season"]
                         and s.get("averageStats")), None)
            if not proj or proj.get("40", proj.get("28", 0)) <= 0:
                continue
            source = "espn_proj"
            flags.append("ROOKIE/NO-DATA")
            notes.append("no NBA logs: ESPN stat line converted with our weights")
            mpg = float(proj.get("40", proj.get("28")))
            pg = {name_: float(proj.get(sid, 0.0)) for sid, name_ in STAT_IDS.items()}
            pg["OREB"] = pg["OREB"] or 0.25 * pg["REB"]
            pg["FTMI"] = pg["FTMI"] or max(pg["FTA"] - pg["FTM"], 0)
            pg["3PMI"] = pg["3PMI"] or max(pg["3PA"] - pg["3PM"], 0)
            pg["TF"] = pg["TF"] or prior["TF"] * mpg
            rates = {c: pg[c] / mpg for c in RATE_STATS}
            second = sorted([pg["PTS"], pg["REB"], pg["AST"]])[1]
            dd = float(np.interp(second, dd_curve.x, dd_curve.y))
            bonus = {"DD": dd, "TD": 0.0, "QD": 0.0}
            mpg_hist = mpg
            g_proj = min(float(proj.get("42", 66) or 66), 66)
            cur_gp, cur_fppg = 0, np.nan
            # ESPN rookie lines tend to run hot; shade 10% until real games exist
            rates = {c: v * (0.9 if W.get(c, 0) > 0 else 1.0) for c, v in rates.items()}
            notes.append("rookie positives shaded -10%")
            last_fppg, last_gp = np.nan, 0

        if nn in ov_min:
            mpg = float(ov_min[nn])
            notes.append("MPG override")
        else:
            mpg = float(np.clip(mpg + minutes_age_adj(age), 0, 38))
        af = age_factor(age)
        fppm = sum(rates[c] * float(W.get(c, 0)) for c in RATE_STATS) * af
        min_scale = float(np.clip(mpg / mpg_hist, 0.6, 1.3)) ** 1.5 if mpg_hist > 0 else 1.0
        bonus_pg = sum(bonus[c] * float(W.get(c, 0)) for c in BONUS_STATS) * min_scale
        fppg = mpg * fppm + bonus_pg

        # injuries / suspensions / unsigned
        miss = 0.0
        if nn in ov_games:
            miss = float(ov_games[nn])
            notes.append(f"override: misses {miss:.0f} G")
        elif inj in ("OUT", "INJURY_RESERVE"):
            # read ESPN's latest note: a rest / load-management day is not a multi-week injury
            latest = next(iter(_news(pid)), None)
            text = (latest["headline"] + " " + latest["story"]).lower() if latest else ""
            if any(w in text for w in ("rest", "management", "maintenance", "load", "back-to-back", "back to back")) and not any(
                    w in text for w in ("surgery", "fracture", "tear", "sprain", "strain", "weeks")):
                miss = float(pcfg["injury_dtd_games_default"])
                notes.append(f"OUT for rest/management per ESPN news ({latest['date']}): {miss:.0f} G")
            else:
                miss, how = _games_from_news(text, float(pcfg["injury_out_games_default"]))
                notes.append(f"OUT: {miss:.0f} G missed ({how})"
                             + (f"; ESPN {latest['date']}: {latest['headline'][:80]}" if latest else ""))
        elif inj == "DAY_TO_DAY":
            miss = float(pcfg["injury_dtd_games_default"])
        if nn in ov_susp:
            miss += float(ov_susp[nn])
            flags.append("SUSPENDED")
        if inj == "SUSPENSION":
            flags.append("SUSPENDED")
        if p.get("proTeamId", 0) == 0:
            flags.append("NO-TEAM")
            g_proj *= 0.5
        if age >= 33:
            flags.append(f"AGE{int(age)}")
        g_proj = max(g_proj - miss, 0)

        rows.append({
            "id": pid, "name": name, "team": team, "pos": "/".join(pos),
            "primary": DEFAULT_POS.get(p.get("defaultPositionId"), "?"),
            "c_elig": "C" in pos, "age": age, "inj": inj if inj != "ACTIVE" else "",
            "g_proj": round(g_proj, 1), "mpg": round(mpg, 1), "fppm": round(fppm, 3),
            "bonus_pg": round(bonus_pg, 2), "dd_pg": round(bonus["DD"], 3), "td_pg": round(bonus["TD"], 3),
            "fppg": round(fppg, 2), "fp_total": round(fppg * g_proj, 1),
            "last_fppg": round(last_fppg, 2) if not np.isnan(last_fppg) else np.nan, "last_gp": last_gp,
            "cur_gp": cur_gp, "cur_fppg": round(cur_fppg, 2) if not np.isnan(cur_fppg) else np.nan,
            **{f"{c.lower()}_pg": round(rates[c] * mpg, 2) for c in ("PTS", "REB", "AST", "STL", "BLK", "TO", "3PM", "FGM", "FGA", "FTM", "FTA")},
            "fg_pct": round(rates["FGM"] / rates["FGA"], 3) if rates["FGA"] else np.nan,
            "ft_pct": round(rates["FTM"] / rates["FTA"], 3) if rates["FTA"] else np.nan,
            "tov_cost_pg": round(rates["TO"] * mpg * 2, 2),
            "ft_miss_cost_pg": round(rates["FTMI"] * mpg * 1, 2),   # FTA -1 per miss
            "adp": round(adp, 1) if adp == adp else np.nan, "espn_rank": espn_rank,
            "flags": " ".join(flags), "notes": "; ".join(notes), "source": source,
            "outlook": (p.get("seasonOutlook") or "").replace("\n", " "),
        })
    df = pd.DataFrame(rows)
    return add_vorp_and_tiers(df, cfg)


def add_vorp_and_tiers(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    n_drafted = cfg["teams"] * cfg["roster"]["size"]
    df = df.sort_values("fp_total", ascending=False).reset_index(drop=True)
    top = set(df.index[:n_drafted])
    repl = {}
    for pos in ("PG", "SG", "SF", "PF", "C"):
        pool = df[(~df.index.isin(top)) & df.pos.str.contains(pos)]
        repl[pos] = float(pool.fp_total.iloc[0]) if len(pool) else 0.0

    def vorp(row):
        return max(row.fp_total - repl[p] for p in row.pos.split("/"))

    df["vorp"] = df.apply(vorp, axis=1).round(1)
    df = df.sort_values("vorp", ascending=False).reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)
    # ESPN gives ~140 to players nobody drafts; treat those as "undrafted" (no edge number)
    drafted = df.adp < 135
    df["adp_rank"] = df.adp.where(drafted).rank(method="min")
    df["edge"] = (df.adp_rank - df["rank"]).where(drafted)  # + = we like him more than ESPN drafters

    # tiers: break where the VORP gap is unusually large (top 200 only)
    v = df.vorp.values
    tiers = np.zeros(len(df), dtype=int)
    t = 1
    for i in range(len(df)):
        if i > 0:
            gap = v[i - 1] - v[i]
            window = v[max(0, i - 6):i + 6]
            typical = np.mean(np.abs(np.diff(window))) if len(window) > 2 else gap
            size = int((tiers[:i] == t).sum())
            if (gap > 1.8 * typical and size >= 2) or size >= 12:
                t += 1
        tiers[i] = t
    df["tier"] = tiers
    df.attrs["replacement"] = repl
    return df


def save_board(df: pd.DataFrame, cfg: dict) -> str:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    path = PROCESSED / f"{cfg['key']}_board.csv"
    df.to_csv(path, index=False)
    repl = df.attrs.get("replacement", {})
    pd.Series(repl).to_csv(PROCESSED / f"{cfg['key']}_replacement.csv", header=["fp_total"])
    return str(path)
