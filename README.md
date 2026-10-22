# Moneyball Fantasy NBA Agent (ESPN)

Stats-first help for two ESPN leagues in the 2026-27 season. Two configs, never mixed:

| Config | League | Format | My team | Status |
|---|---|---|---|---|
| `config/league_a.yaml` | BLR Dreaming FNBA (id 650703513) | 6-team H2H **9-cat** | Okc Champs 2027 | In season: category model, waiver + trade tools |
| `config/league_b.yaml` | Ball Don't Lie (id 1129175968) | 10-team H2H **points** | Okc champs 2027 | Drafted 20 Oct 2026 (pick 3). Weekly projection in progress |

Every report starts with the league name. Default mode is **recommend only**: the tool never adds, drops,
trades or changes a lineup. Every action you take is logged in `actions.log` (git-ignored).

## Setup

```bash
python3 -m pip install -r requirements.txt
python3 refresh.py            # ESPN game logs + injuries + ADP, ages; rebuilds the player values (~1 min)
python3 -m pytest -q tests    # 7 tests
```

### ESPN login cookie (private leagues)

Both leagues are private. Put your cookie in a `.env` file in the project root (template: `config/.env.example`):

1. Log in at fantasy.espn.com, press F12 → **Application** → **Cookies** → `https://fantasy.espn.com`.
2. Copy the value of `espn_s2` into `ESPN_S2=`. `ESPN_SWID` is optional (`espn_s2` alone is enough to read a league).

`.env` is git-ignored and the tool never prints it. Treat the value like a password; logging out of ESPN in that
browser ends it.

## Commands

| Command | What it does |
|---|---|
| `python3 refresh.py [--force]` | Pull ESPN player data (cached 12 h; past seasons 30 days) and rebuild `data/processed/league_b_board.csv` |
| `python3 draft.py` | League B live draft board (used on draft night; `h` for help) |
| `python3 mock_draft.py --slot N` | Practice draft against ADP bots |
| `python3 league_a.py --sync` | Pull League A rosters + free-agent/waiver list from ESPN, then write `reports/league_a_<date>.md` |
| `python3 league_a.py` | League A report from the last sync: category ranks, per-player z-scores, waiver adds, trade ideas |
| `python3 league_a.py --eval Toronto "Brunson,McDaniels" "Jamal Murray,Mikal Bridges"` | Score any trade or counter in about a second: new rank, category changes, ACCEPT/DECLINE |

## How players are valued

All numbers come from real game logs (ESPN, 3 seasons, ~78k player-games). ESPN's own projections are used only
for rookies with no NBA games (shaded −10%, flagged `ROOKIE/NO-DATA`).

* **Per-minute production** blended 2025-26 (60%) / 2024-25 (28%) / 2023-24 (12%), shrunk toward a bench-level
  baseline when the sample is small.
* **Minutes**: season blend 75% + last 20 games 25%, with an age adjustment.
* **Age**: young players +1–4% per minute; 31+ decline gently.
* **Games**: 3-season games-played history, regressed toward the league norm, minus current injury
  (OUT = −15 games unless overridden in config) and suspensions.

### League B (points)

Exact league weights: FGM +2, FGA −1, FTM +1, FTA −1, FTMI −1, 3PM +1, 3PMI −1, OREB +1, REB +1.5, AST +2.5,
STL +4, BLK +4, TO −2, TF −1, PTS +1, DD +4, TD +7.5, QD +10. (A missed free throw is −2 with these weights.)
DD/TD/QD rates are counted from game logs. Season value = FP/G × projected games; VORP = value minus the best
player at the same position left after 130 picks.

`src/weekly.py` projects a matchup week: games per player from ESPN's pro schedule
(`data/raw/pro_schedule_2027.json`) and a daily lineup that fits PG/SG/SF/PF/C/G/F + 3 UTIL.

### League A (9-cat)

Per-game z-scores in FG%, FT%, 3PM, REB, AST, STL, BLK, TO, PTS. FG% and FT% are scored by volume impact
(makes above league average × attempts). TO is inverted. Teams are compared by expected categories won per week
against the other five teams. Trade ideas must (1) help me, (2) look like a win to the other manager by ESPN draft
position (convex, star-weighted), and (3) still help me if players miss games at their history-based rate.

## League rules (read from ESPN settings)

| | League A | League B |
|---|---|---|
| Waivers | FAAB $200, min $1, processed **Sun 09:00 ET (21:00 HKT)**; dropped players wait ≥48 h | Rolling order that **never resets**; a claim sends you last; 1-day waiver period |
| Add limit | none | **1 add per day** (max 7 a week) |
| IR | 2 | 1 |
| Position limit | none | **max 3 centers** (by ESPN primary position) |
| Trade deadline / review | 6 Apr 2027 / 1 day, 4 vetoes | 20 Feb 2027 (01:00 HKT) / 1 day, 4 vetoes |

## Files

```
config/            league_a.yaml, league_b.yaml, .env.example
data/raw/          ESPN + Basketball Reference downloads (git-ignored), league settings, pro schedule, scoreboards
data/processed/    league_b_board.csv (all player values), replacement levels
data/league_a_rosters/  espn_rosters.yaml (latest ESPN sync), rosters.yaml + screenshot (draft day)
data/league_b_rosters/  draft_results_2026-10-20.json (all 130 picks, matches ESPN)
reports/           league_a_<date>.md, league_b_draft_queue.md, day1_recap_2026-10-20.md
src/               common, espn_public, espn_league, bbref, projections, draft, categories, weekly
```

## Data sources

| Source | Used for | Status |
|---|---|---|
| ESPN public fantasy feed | game logs, injuries, positions, ADP | works |
| ESPN private league API (cookie) | rosters, waivers, transactions, scores, draft results | works. Picks are hidden **during** a live draft |
| Basketball Reference (1 page/season, cached 30 days) | ages | works (fetched with `curl`; it blocks python-requests) |
| `nba_api` / stats.nba.com | usage, tracking | blocked from cloud servers; not used |

## What works and what doesn't yet

Works:
* League B draft board, autodraft queue, mock drafts (done; draft finished 20 Oct)
* League A ESPN sync, category report, waiver suggestions, trade search, `--eval` for counters
* League B week projection and daily lineup (run from Python for now; no command yet)

Not yet / known limits:
* **No scheduled refresh** yet (`refresh.py` runs by hand). Planned: daily, plus Sunday before League A waivers.
* `refresh.py` does not re-download the pro schedule; it was fetched once into `data/raw/pro_schedule_2027.json`.
* No rolling 7/14/30-day form or changelog yet.
* League B trade search and waiver advice are run ad hoc, not from a command.
* Games projection is too harsh on players coming back from one long injury (e.g. Tatum projects 45 G).
* Rosters in `league_a.yaml` / `league_b` draft file are draft-day snapshots; use `--sync` for current rosters.
* Auto mode (lineup changes without approval) is not built; `auto_mode: false` in both configs.
