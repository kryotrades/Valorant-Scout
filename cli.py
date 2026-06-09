#!/usr/bin/env python3
"""
cli.py — Valorant Scout in your terminal.

A console scoreboard: every player in your current match with
Party, Agent, Name (hidden names revealed), Rank, RR, Peak, Previous, Leaderboard,
HS%, Win-rate, K/D and Level — colour-coded and auto-refreshing.

Pulls live data straight from the local VALORANT client (open the game and join
agent select / a match). With the game closed it shows a demo lobby.

Usage:
  python cli.py                 # live table, refreshes every 5s
  python cli.py --once          # print once and exit
  python cli.py --interval 3    # custom refresh seconds
  python cli.py --seed 12       # pick a demo lobby
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))


def _load_env():
    for p in (ROOT / ".env", ROOT / "backend" / ".env"):
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env()

try:
    from rich import box
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
except ImportError:
    print("This view needs 'rich'.  Install with:  pip install rich")
    sys.exit(1)

import sample_match  # noqa: E402
from riot_client import LocalAuth  # noqa: E402

# Windows consoles default to cp1252 — force UTF-8 so glyphs/colours render.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

console = Console()


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def build_board(seed: int) -> dict:
    pref = os.environ.get("DATA_SOURCE", "auto").lower()
    if pref != "demo" and LocalAuth.available():
        try:
            import live_match
            board = live_match.LiveMatch(LocalAuth()).build_scoreboard(
                include_stats=os.environ.get("LIVE_INCLUDE_STATS", "true").lower() != "false")
            board.setdefault("sourceDetail", "Local VALORANT client")
            return board
        except Exception as e:  # noqa: BLE001 - game running but unreadable
            return {"state": "OFFLINE", "stateLabel": "Offline", "source": "local",
                    "error": str(e), "players": [], "teams": {}, "parties": [],
                    "notice": {"level": "warn", "action": "restart_game",
                               "message": "Couldn't read VALORANT — please restart "
                                          "your game (close it completely and relaunch)."}}
    board = sample_match.generate(seed)
    if pref != "demo" and not LocalAuth.available():
        board["notice"] = {"level": "info", "action": "open_game",
                           "message": "Open VALORANT for live data (showing demo for now)."}
    return board


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def kd_color(kd):
    if kd is None:
        return "grey50"
    if kd >= 1.3:
        return "#18E5A7"
    if kd >= 1.0:
        return "#9ADEFF"
    if kd >= 0.8:
        return "#ECE8E1"
    return "#FF8088"


def _cell_party(p):
    if p.get("party"):
        return Text(f"●{p['party']['number']}", style=f"bold {p['party']['color']}")
    return Text("")


def _cell_name(p, team_color):
    style = p["party"]["color"] if p.get("party") else team_color
    txt = Text(p["name"], style=style)
    if p["isSelf"]:
        txt.stylize("bold underline")
    if p["nameHidden"]:
        txt.append("  (hidden)", style="italic #FF4655")
    return txt


def _cell_peak(p):
    """Peak rank with the act it was hit on a dim second line (e.g. 'V25 Act 1')."""
    txt = Text(p["peakRank"], style=p["peakColor"])
    if p.get("peakAct"):
        txt.append(f"\n{p['peakAct']}", style="grey50")
    return txt


def _add_team(table, players, team_color):
    for p in players:
        rank_style = "grey42" if (p["rankTier"] or 0) <= 2 else p["rankColor"]
        lb = f"#{p['leaderboard']:,}" if p.get("leaderboard") else "—"
        table.add_row(
            _cell_party(p),
            Text(p["agent"] or "—", style=p["agentColor"]),
            _cell_name(p, team_color),
            Text(p["rank"], style=rank_style),
            Text(str(p["rr"]) if (p["rankTier"] or 0) > 2 else "—", style="grey70"),
            _cell_peak(p),
            Text(p["previousRank"], style="grey50"),
            Text(lb, style="#FFD75F" if p.get("leaderboard") else "grey37"),
            Text(f"{p['hsPct']}%" if p.get("hsPct") is not None else "—", style="grey70"),
            Text(f"{p['winRate']}% ({p['games']})",
                 style="#18E5A7" if p["winRate"] >= 50 else "grey62"),
            Text(str(p["kd"]) if p.get("kd") is not None else "—", style=kd_color(p.get("kd"))),
            Text(f"{p['level']}{' *' if p['levelHidden'] else ''}", style="grey58"),
        )


def render(board) -> Group:
    state = board.get("state")
    is_demo = board.get("source") == "demo"
    src = "[#FFB454]DEMO[/]" if is_demo else "[#18E5A7]LIVE · LOCAL CLIENT[/]"

    state_styles = {"INGAME": "[bold #FF4655]● LIVE · IN GAME[/]",
                    "PREGAME": "[bold #FFB454]◆ AGENT SELECT[/]",
                    "MENUS": "[#18E5A7]◆ IN LOBBY[/]", "OFFLINE": "[grey62]OFFLINE[/]"}
    head = state_styles.get(state, "[grey62]—[/]")
    if board.get("map"):
        head += f"   [bold #ECE8E1]{board['map']}[/]   [grey62]{board.get('mode','')}[/]"
    if board.get("score"):
        sc = board["score"]
        head += f"   [#18E5A7]{sc['ally']}[/][grey50]:[/][#FF4655]{sc['enemy']}[/] [grey50]RD {sc['round']}[/]"
    if board.get("lockProgress"):
        lp = board["lockProgress"]
        head += f"   [#FFB454]{lp['locked']}/{lp['total']} locked[/]"
    head += f"   {src}"

    notice = board.get("notice")
    if not board.get("players"):
        msg = ((notice or {}).get("message") or board.get("error")
               or "Open VALORANT — lobby, Agent Select or a match.")
        return Group(Panel(Text(f"No players to show.\n{msg}", justify="center"),
                           title="VALORANT SCOUT", border_style="#FF4655", box=box.HEAVY))

    table = Table(box=box.SIMPLE_HEAVY, expand=False, show_edge=False, pad_edge=False,
                  header_style="bold #7E8C92", border_style="grey23")
    table.add_column("P", justify="center", width=3)
    table.add_column("Agent", width=10, no_wrap=True)
    table.add_column("Name", width=22, no_wrap=True)
    table.add_column("Rank", width=13, no_wrap=True)
    table.add_column("RR", justify="right", width=4)
    table.add_column("Peak", width=14, no_wrap=True)
    table.add_column("Prev", width=11, no_wrap=True)
    table.add_column("LB", justify="right", width=7)
    table.add_column("HS%", justify="right", width=5)
    table.add_column("WR", justify="right", width=11)
    table.add_column("K/D", justify="right", width=5)
    table.add_column("Lvl", justify="right", width=6)

    teams = board.get("teams", {})
    self_team = board.get("selfTeam", "Blue")
    other = next((t for t in teams if t != self_team), None)

    _add_team(table, teams.get(self_team, []), "#18E5A7")
    if state == "INGAME" and other:
        table.add_section()
        _add_team(table, teams.get(other, []), "#FF4655")

    parts = board.get("parties", [])
    legend = Text("  ")
    if parts:
        legend.append("Parties:  ")
        for p in parts:
            legend.append(f"●{p['number']} ", style=f"bold {p['color']}")
            legend.append(f"{p['size']}-stack   ", style="grey62")

    panel = Panel(table, title="[bold #FF4655]VALORANT[/] [bold #ECE8E1]SCOUT[/]",
                  subtitle=head, border_style="#FF4655", box=box.HEAVY, padding=(0, 1))
    rows = [panel, legend]
    if notice:
        tone = "#FF4655" if notice.get("level") == "warn" else "#FFB454"
        rows.append(Text(f"  ⚠ {notice['message']}", style=tone))
    return Group(*rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="Valorant Scout terminal scoreboard")
    ap.add_argument("--once", action="store_true", help="print once and exit")
    ap.add_argument("--interval", type=float, default=5.0, help="refresh seconds")
    ap.add_argument("--seed", type=int, default=7, help="demo lobby seed")
    # Ignore unknown flags (run.py forwards its own flags like --prod when it
    # opens the CLI in a separate console; argparse would otherwise crash it).
    args, _ = ap.parse_known_args()

    if args.once:
        console.print(render(build_board(args.seed)))
        return

    # screen=True uses the alternate buffer and redraws the whole view each
    # frame, so resizing the terminal re-flows cleanly instead of garbling.
    try:
        with Live(render(build_board(args.seed)), console=console,
                  refresh_per_second=4, screen=True, transient=False) as live:
            while True:
                time.sleep(max(1.0, args.interval))
                live.update(render(build_board(args.seed)))
    except KeyboardInterrupt:
        console.print("[grey50]gg — bye.[/]")


if __name__ == "__main__":
    main()
