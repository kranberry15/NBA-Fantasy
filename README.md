# Moneyball Fantasy NBA Agent (ESPN)

Two leagues, two configs, never mixed:

| Config | League | Format | Status |
|---|---|---|---|
| `config/league_b.yaml` | Ball Don't Lie | 10-team H2H **points**, snake draft Oct 20 2026 23:30 HKT | **Draft assistant ready** |
| `config/league_a.yaml` | BLR Dreaming FNBA | 6-team H2H **9-cat** | Drafted. In-season tools: next phase |

## Setup (5 minutes)

```bash
git clone <this repo> && cd NBA-Fantasy
python3 -m pip install -r requirements.txt
python3 refresh.py          # downloads data + builds the board (~1 min first time)
python3 -m pytest -q tests  # should say "7 passed"
python3 mock_draft.py --slot 4   # practice run
python3 draft.py            # the real thing
```

A ready-built board is committed in `data/processed/league_b_board.csv`, so `draft.py` works even
if `refresh.py` cannot reach the internet.

### ESPN cookies (only needed for live draft sync and in-season features)

1. Log in at fantasy.espn.com and open the league.
2. F12 → **Application** → **Cookies** → `https://fantasy.espn.com`.
3. Copy `espn_s2` and `SWID` (keep the `{}` braces) into a file named `.env` in the project root
   (template: `config/.env.example`). `.env` is git-ignored and the tool never prints it.
4. Put the league id (the `leagueId=` number in the league URL) in `config/league_b.yaml → league_id`.

## Draft night (League B)

1. ~22:30 HKT ESPN sets the order. Run `python3 draft.py`, type `slot N` (your pick number).
   It prints your 13 pick numbers (snake).
2. Each time anyone picks, type the player name (partial ok: `sengun`, `jalen j`). The board updates.
   If cookies + league_id are set, type `s` instead to pull all picks from ESPN.
3. On your turn the tool prints **PICK + 2 alternatives** with the numbers behind them, and which tiers
   will be gone before your next pick. Commands: `b` board (`b C`, `b PG edge`), `t` tiers, `m` my roster,
   `i name` player detail, `undo`, `log`, `h` help.
4. State is saved after every pick (`data/draft_state_league_b.json`), so a crash loses nothing: just restart.

## How players are valued (League B)

Every game in the last 3 seasons (ESPN game logs, ~78k player-games) is scored with the exact league weights:

FGM +2, FGA −1, FTM +1, FTA −1, FTMI −1, 3PM +1, 3PMI −1, OREB +1, REB +1.5, AST +2.5, STL +4, BLK +4,
TO −2, TF −1, PTS +1, DD +4, TD +7.5, QD +10.

* **Per-minute production** blended 2025-26 (60%) / 2024-25 (28%) / 2023-24 (12%), shrunk toward a
  bench-player baseline when the sample is small (250-minute prior).
* **Minutes**: season blend 75% + last 20 games 25%, ±age adjustment. Override in config.
* **DD / TD / QD** rates are counted from game logs (ESPN's own flags), not guessed from averages.
* **Age**: young players +1–4% per minute, 31+ decline gently (−1% to −6%).
* **Games**: 3-season games-played history, regressed toward 64 G; minus injury (OUT = −15 G unless
  you set `overrides.games_missed`), suspensions, unsigned players (−50%).
* **Season value** = FP/G × projected games. **VORP** = value minus the best player at his position
  left after 130 picks (best of his eligible positions).
* **Tiers** break where the VORP gap is unusually large.
* **Recommendation** = VORP now + expected best VORP still available at your next pick (opponents
  modelled to draft in ESPN ADP/rank order). This is what makes it skip a player who will still be
  there next round. Hard rules: never a 4th center (primary position C), and late in the draft it only
  suggests players who fill an empty PG/SG/SF/PF/C/G/F slot.
* Flags: `OUT`/`DAY_TO_DAY`/`SUSPENDED` (red), `NEW-TEAM`, `MIN↑/↓` (last-20 minutes changed ≥4),
  `USG↑/↓` (shots+FT+TO per 36 changed), `AGE33+`, `SMALL-SAMPLE`, `ROOKIE/NO-DATA`.

## Data sources

| Source | Used for | Status |
|---|---|---|
| ESPN public fantasy feed (same API as `espn-api`) | game logs, injuries, positions, ADP | works |
| Basketball Reference (1 page per season, cached 30 days) | ages | works |
| `nba_api` / stats.nba.com | usage, tracking | blocked from cloud servers; will try from your PC in phase 2 |
| ESPN projections | **only** for 2026 rookies with no NBA games (shaded −10%) | flagged `ROOKIE/NO-DATA` |

Raw downloads are cached in `data/raw/` (git-ignored); game logs also go to `data/cache.sqlite`.

## Safety

Default mode is recommend-only. Nothing is ever added, dropped, traded or set without your approval
(`auto_mode: false` in each config).
