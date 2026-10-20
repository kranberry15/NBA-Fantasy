#!/usr/bin/env python3
"""League B live draft assistant.  Run:  python draft.py   (type `h` for help)

Type a player name to record the next pick (whoever is on the clock).
"""
from __future__ import annotations

import sys
import time

import pandas as pd
from rich.console import Console
from rich.table import Table

from src.common import header, load_env, load_league
from src.draft import Draft, snake_slot

con = Console(highlight=False, width=max(Console().width, 150))

HELP = """
[bold]Commands[/bold]
  slot N            set your draft position (1-10). Shows all your pick numbers.
  <player name>     record the next pick (the team on the clock). Partial names ok: "sengun", "jalen j"
  undo              remove the last recorded pick
  r                 best pick now (+2 alternatives) with reasons
  b [POS] [sort]    board of best available. POS = PG/SG/SF/PF/C. sort = vorp|fppg|fp|adp|edge
  t                 tiers: how many left now and how many expected at your next pick
  m                 my roster, slots filled, center count
  i <name>          player detail (stats, flags, ESPN outlook)
  log               all picks so far
  s                 sync picks from ESPN (needs league_id in config + cookies in .env)
  reset             clear all picks (asks to confirm)
  h                 help        x   exit
"""

SORTS = {"vorp": ("vorp", False), "fppg": ("fppg", False), "fp": ("fp_total", False),
         "adp": ("adp", True), "edge": ("edge", False)}


def fmt_flags(r) -> str:
    out = []
    if isinstance(r.inj, str) and r.inj:
        out.append(f"[bold red]{r.inj}[/bold red]")
    if isinstance(r.flags, str) and r.flags:
        f = r.flags
        f = f.replace("SUSPENDED", "[bold red]SUSPENDED[/bold red]")
        out.append(f"[yellow]{f}[/yellow]")
    return " ".join(out)


def board_table(d: Draft, pos: str | None = None, sort: str = "vorp", n: int = 25, title: str = "Best available"):
    av = d.available()
    if pos:
        av = av[av.pos_list.apply(lambda pl: pos in pl)]
    col, asc = SORTS.get(sort, SORTS["vorp"])
    av = av.sort_values(col, ascending=asc, na_position="last").head(n)
    on_clock = bool(d.my_slot) and snake_slot(d.current, d.teams) == d.my_slot
    nxt = d.next_my_pick(d.current + 1 if on_clock else d.current)
    # opponent picks before that pick (if I'm on the clock, my own pick now is not one of them)
    between = (nxt - d.current - (1 if on_clock else 0)) if nxt else 0
    surv = d.survival(d.available(), between)
    t = Table(title=f"{title} (sorted by {col})", show_lines=False, pad_edge=False)
    for c in ["#", "Player", "Tm", "Pos", "Age", "G", "MPG", "FP/G", "Season", "VORP", "Tier", "ADP",
              "Edge", f"@{nxt or '-'}", "Flags"]:
        t.add_column(c, justify="right" if c not in ("Player", "Pos", "Flags", "Tm") else "left",
                     no_wrap=c != "Flags")
    for pid, r in av.iterrows():
        t.add_row(str(r["rank"]), r["name"], r.team, r.pos, f"{r.age:.0f}" if r.age == r.age else "?",
                  f"{r.g_proj:.0f}", f"{r.mpg:.0f}", f"{r.fppg:.1f}", f"{r.fp_total:.0f}", f"{r.vorp:.0f}",
                  str(r.tier), f"{r.adp:.0f}" if r.adp == r.adp else "-",
                  f"{r.edge:+.0f}" if r.edge == r.edge else "-", f"{surv[pid]:.0%}", fmt_flags(r))
    con.print(t)
    con.print(f"[dim]@{nxt or '-'} = chance he is still there at your next pick. Edge + = we rank him higher than ESPN ADP.[/dim]")


def show_status(d: Draft):
    if d.done:
        con.print("[bold green]Draft complete.[/bold green]")
        return
    cur = d.current
    slot = snake_slot(cur, d.teams)
    rnd = (cur - 1) // d.teams + 1
    who = "[bold green]YOU[/bold green]" if slot == d.my_slot else d.team_names.get(slot, f"slot {slot}")
    nxt = d.next_my_pick()
    away = f" | your next pick: {nxt} ({nxt - cur} away)" if nxt else ""
    con.print(f"On the clock: pick {cur} (round {rnd}) -> {who}{away}")


def show_rec(d: Draft):
    t0 = time.time()
    if not d.my_slot:
        con.print("Set your slot first: slot N")
        return
    if snake_slot(d.current, d.teams) != d.my_slot:
        nxt = d.next_my_pick()
        if not nxt:
            con.print("You have no picks left.")
            return
        con.print(f"Not your pick yet (pick {d.current}). Players likely there at your pick {nxt}:")
        board_table(d, n=10, title="Preview")
        return
    rec = d.recommend()
    plan = rec["plan"]
    if plan.empty:
        con.print("No eligible players.")
        return
    nxt = rec["next_pick"]
    con.print(f"[bold]BEST PICK NOW[/bold] (pick {d.current}; you pick again at {nxt or '-'}, "
              f"{rec['between']} picks in between)")
    for i, row in enumerate(plan.itertuples()):
        label = "PICK" if i == 0 else f"ALT{i}"
        name = d.board.at[row.id, "name"]
        con.print(f" [bold cyan]{label}: {name}[/bold cyan] ({d.board.at[row.id, 'pos']})  "
                  f"plan value {row.plan:.0f} = VORP {row.vorp:.0f} + expected best at next pick {row.follow:.0f}")
        con.print(f"    {d.reason(row.id, row.survive, nxt)}")
    top_vorp = d.eligible_for_me(d.available()).sort_values("vorp", ascending=False).iloc[0]
    if top_vorp.id != plan.iloc[0].id:
        con.print(f" [dim]Note: {top_vorp['name']} has the top VORP but is likely to last "
                  f"({rec['surv'][top_vorp.id]:.0%}) - take him later.[/dim]")
    warn_centers(d)
    out = d.tier_outlook(after_taking=int(plan.iloc[0].id), max_tiers=5)
    if not out.empty:
        con.print("Tiers if you take the PICK: " + " | ".join(
            f"T{r.tier}: {r.left_now} left -> ~{r.exp_left_at_next} at your next" for r in out.itertuples()))
    con.print(f"[dim]({(time.time() - t0) * 1000:.0f} ms)[/dim]")


def warn_centers(d: Draft):
    prim, elig = d.center_counts()
    if d.max_c and prim >= d.max_c:
        con.print(f"[bold red]CENTER CAP: you have {prim}/{d.max_c} centers. Centers are hidden from recommendations.[/bold red]")
    elif d.max_c and prim == d.max_c - 1:
        con.print(f"[bold yellow]WARNING: {prim}/{d.max_c} centers. One more C and you hit the cap.[/bold yellow]")
    if elig > prim:
        con.print(f"[dim]C-eligible on roster (incl. PF/C): {elig}. Cap counted on primary position.[/dim]")


def show_roster(d: Draft):
    r = d.my_roster()
    con.print(f"My roster ({len(r)}/{d.cfg['roster']['size']}), slot {d.my_slot}, picks {d.my_picks()}")
    t = Table()
    for c in ["Player", "Pos", "Primary", "FP/G", "G", "Season", "VORP", "Flags"]:
        t.add_column(c)
    for _, x in r.iterrows():
        t.add_row(x["name"], x.pos, x.primary, f"{x.fppg:.1f}", f"{x.g_proj:.0f}", f"{x.fp_total:.0f}",
                  f"{x.vorp:.0f}", fmt_flags(x))
    con.print(t)
    filled, missing = d.roster_fill_status()
    con.print(f"Position slots filled: {filled}/7 (PG,SG,SF,PF,C,G,F). Still need: {', '.join(missing) or 'none'}. "
              f"UTIL x3 + bench take anyone. Total projected: {r.fp_total.sum():.0f} pts")
    warn_centers(d)


def show_info(d: Draft, text: str):
    ids = d.find(text, d.board)
    if not ids:
        con.print("No match.")
        return
    r = d.board.loc[ids[0]]
    taken = next((p for p in d.picks if p["id"] == r.id), None)
    con.print(f"[bold]{r['name']}[/bold] {r.team} {r.pos} age {r.age} | {'TAKEN pick ' + str(taken['n']) if taken else 'available'}")
    con.print(f" Proj: {r.mpg} MPG, {r.fppg} FP/G x {r.g_proj} G = {r.fp_total} | VORP {r.vorp} rank {r['rank']} tier {r.tier}")
    con.print(f" Per game: {r.pts_pg} PTS {r.reb_pg} REB {r.ast_pg} AST {r.stl_pg} STL {r.blk_pg} BLK "
              f"{r.to_pg} TO {r['3pm_pg']} 3PM | FG {r.fg_pct:.3f} FT {r.ft_pct:.3f}")
    con.print(f" Bonus {r.bonus_pg}/g (DD {r.dd_pg:.2f}/g, TD {r.td_pg:.2f}/g) | TO cost {r.tov_cost_pg}/g, "
              f"FT-miss cost {r.ft_miss_cost_pg}/g")
    con.print(f" Last season: {r.last_fppg} FP/G in {r.last_gp} G | ADP {r.adp} | ESPN pts rank {r.espn_rank}")
    con.print(f" Flags: {fmt_flags(r) or '-'}  Notes: {r.notes if isinstance(r.notes, str) else '-'}")
    if isinstance(r.outlook, str):
        con.print(f" [dim]ESPN outlook: {r.outlook[:600]}[/dim]")
    if len(ids) > 1:
        con.print(f" [dim]Other matches: {', '.join(d.board.at[i, 'name'] for i in ids[1:5])}[/dim]")


def do_sync(d: Draft):
    from src.espn_league import fetch_draft
    cfg = d.cfg
    try:
        info = fetch_draft(cfg, load_env())
    except Exception as e:
        con.print(f"[red]ESPN sync failed: {e}[/red]\nKeep typing picks by hand.")
        return
    if not info.get("order_final"):
        con.print("[yellow]ESPN has not set the draft order yet (it does so 1 hour before the draft). "
                  "Run `s` again after that.[/yellow]")
        return
    if info.get("my_slot"):
        d.my_slot = info["my_slot"]
    d.team_names = info.get("team_names", {})
    added = 0
    known = {p["n"] for p in d.picks}
    for n, pid in info["picks"]:
        if n in known:
            continue
        if n != d.current:
            con.print(f"[yellow]Pick numbering mismatch at {n} (local {d.current}). Run reset then s.[/yellow]")
            break
        if pid in d.board.index:
            d.record(pid)
        else:
            d.picks.append({"n": n, "slot": snake_slot(n, d.teams), "id": int(pid), "name": f"ESPN#{pid}"})
        added += 1
    d.save()
    con.print(f"Synced: {added} new picks. Your slot: {d.my_slot}.")


def handle_pick(d: Draft, text: str):
    ids = d.find(text)
    if not ids:
        con.print(f"[red]No available player matches '{text}'.[/red] (already drafted? try `i {text}`)")
        return
    if len(ids) > 1 and d._norm[ids[0]] != text.lower():
        top = ids[:6]
        con.print("Several matches: " + "; ".join(f"[{j + 1}] {d.board.at[i, 'name']} ({d.board.at[i, 'team']})"
                                                     for j, i in enumerate(top)))
        ans = input("Number (Enter = cancel): ").strip()
        if not ans.isdigit() or not 1 <= int(ans) <= len(top):
            return
        ids = [top[int(ans) - 1]]
    pid = ids[0]
    was_mine = d.my_slot and snake_slot(d.current, d.teams) == d.my_slot
    p = d.record(pid)
    r = d.board.loc[pid]
    who = "YOU" if was_mine else d.team_names.get(p["slot"], f"slot {p['slot']}")
    con.print(f"Pick {p['n']}: {p['name']} -> {who}   (our rank {r['rank']}, VORP {r.vorp:.0f}) {fmt_flags(r)}")
    if was_mine:
        warn_centers(d)
    after_turn(d)


def after_turn(d: Draft):
    show_status(d)
    if d.done or not d.my_slot:
        return
    if snake_slot(d.current, d.teams) == d.my_slot:
        show_rec(d)
    elif d.next_my_pick() - d.current <= 2:
        con.print("[dim]You're up soon. Preview:[/dim]")
        board_table(d, n=8, title="Likely options")


def main():
    cfg = load_league("league_b")
    d = Draft(cfg)
    con.print(f"[bold]{header(cfg)}[/bold]")
    con.print(f"Loaded {len(d.board)} players. Picks recorded: {len(d.picks)}. Your slot: {d.my_slot or 'NOT SET (type: slot N)'}")
    if d.my_slot:
        con.print(f"Your picks: {d.my_picks()}")
    show_status(d)
    while True:
        try:
            line = input("draft> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        cmd, _, arg = line.partition(" ")
        c = cmd.lower()
        try:
            if c in ("x", "exit", "quit"):
                break
            elif c in ("h", "help", "?"):
                con.print(HELP)
            elif c == "slot":
                d.set_slot(int(arg))
                con.print(f"Your slot: {d.my_slot}. Your picks: {d.my_picks()}")
                show_status(d)
            elif c == "undo":
                p = d.undo()
                con.print(f"Removed pick {p['n']}: {p['name']}" if p else "Nothing to undo.")
                show_status(d)
            elif c == "r":
                show_rec(d)
            elif c == "b":
                parts = arg.upper().split()
                pos = next((x for x in parts if x in ("PG", "SG", "SF", "PF", "C")), None)
                sort = next((x.lower() for x in parts if x.lower() in SORTS), "vorp")
                board_table(d, pos, sort)
            elif c == "t":
                out = d.tier_outlook()
                con.print(out.to_string(index=False) if not out.empty else "No tiers left.")
            elif c == "m":
                show_roster(d)
            elif c == "i":
                show_info(d, arg)
            elif c == "log":
                for p in d.picks:
                    mine = " <- YOU" if p["slot"] == d.my_slot else ""
                    con.print(f"{p['n']:>3}. (slot {p['slot']:>2}) {p['name']}{mine}")
            elif c == "s":
                do_sync(d)
                after_turn(d)
            elif c == "reset":
                if input("Type YES to clear all picks: ").strip() == "YES":
                    d.picks = []
                    d.save()
                    con.print("Cleared.")
            elif c == "d":
                handle_pick(d, arg)
            else:
                handle_pick(d, line)
        except Exception as e:  # never crash mid-draft
            con.print(f"[red]Error: {e}[/red]")


if __name__ == "__main__":
    sys.exit(main())
