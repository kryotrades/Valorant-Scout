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
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "backend"))

os.environ["SCOUT_QUIET"] = "1"

# This console is blank while imports/first fetch run — tell the user what it
# is. Live(screen=True) switches to the alternate buffer, replacing this text.
print("\n  Starting Valorant Scout...\n  The scoreboard will appear in this window in a moment.", flush=True)

def _load_env():
    for p in (ROOT / ".env", ROOT / "backend" / ".env"):
        if p.exists():
            # utf-8-sig / replace: hand-edited .env with a BOM or stray byte
            # must not corrupt the first key or crash the CLI at import time.
            for line in p.read_text(encoding="utf-8-sig", errors="replace").splitlines():
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

import sample_match
from riot_client import LocalAuth

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

if os.name == "nt":
    # Enable ANSI/VT on our own fresh console (run.py spawns us with
    # CREATE_NEW_CONSOLE, which starts without it). With VT on, rich uses its
    # modern renderer instead of the flickery legacy-conhost fallback
    # (STD_OUTPUT_HANDLE = -11; mode 7 = processed|wrap|virtual-terminal).
    try:
        import ctypes
        _k32 = ctypes.windll.kernel32
        _k32.SetConsoleMode(_k32.GetStdHandle(-11), 7)
    except Exception:
        pass

console = Console()

def _set_window_title(title: str) -> None:
    # run.py spawns us in a fresh console with no title, so Windows labels the
    # window by its host exe (python.exe / conhost) — inconsistent across PCs.
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        else:
            sys.stdout.write(f"\33]0;{title}\a")
            sys.stdout.flush()
    except Exception:
        pass

# ── Backend bridge (run.py launches us with --bridge) ────────────────────────
# In normal Scout operation the backend is the ONLY Riot fetcher: we render the
# board it broadcasts over the local WebSocket bridge and never touch Riot
# ourselves — not even while disconnected. Standalone `python cli.py` (no flag)
# is the explicit direct-fetch mode and skips all of this.
_BRIDGE_PATH = ROOT / ".scout" / "bridge.json"
_BRIDGE_MODE = False
_BRIDGE_LOCK = threading.Lock()
_BRIDGE = {"board": None, "connected": False}
_BRIDGE_STOP = threading.Event()  # tests only — lets a test retire the thread

def _bridge_loop() -> None:
    from websockets.sync.client import connect as _ws_connect
    while not _BRIDGE_STOP.is_set():
        try:
            info = json.loads(_BRIDGE_PATH.read_text(encoding="utf-8"))
            with _ws_connect(f"ws://127.0.0.1:{int(info['wsPort'])}",
                             open_timeout=5, close_timeout=2) as ws:
                ws.send(json.dumps({"type": "auth", "token": info.get("token", ""),
                                    "protocol": info.get("protocol", 1)}))
                if json.loads(ws.recv(timeout=5)).get("type") != "auth_ok":
                    raise RuntimeError("bridge auth rejected")
                while True:
                    # Server pings every 30s, so a healthy link always yields a
                    # frame; 60s of silence means a hung backend — reconnect.
                    msg = json.loads(ws.recv(timeout=60))
                    if _BRIDGE_STOP.is_set():
                        return
                    mtype = msg.get("type")
                    if mtype == "state" and isinstance(msg.get("data"), dict):
                        with _BRIDGE_LOCK:
                            _BRIDGE["board"] = msg["data"]
                            _BRIDGE["connected"] = True
                    elif mtype == "ping":
                        ws.send(json.dumps({"type": "pong"}))
        except Exception:
            # Covers it all: backend not up yet, stale bridge.json from a
            # previous run, backend restart with a new token, mid-game crash.
            with _BRIDGE_LOCK:
                _BRIDGE["connected"] = False
            time.sleep(2.0)

def _bridge_board() -> dict:
    with _BRIDGE_LOCK:
        board = _BRIDGE["board"]
        connected = _BRIDGE["connected"]
    if board is None:
        return {"state": "OFFLINE", "stateLabel": "Offline", "source": "bridge",
                "players": [], "teams": {}, "parties": [],
                "notice": {"level": "info", "message": "Waiting for backend…"}}
    board = dict(board)
    board["sourceDetail"] = "Backend bridge"
    if not connected:
        board["notice"] = {"level": "warn",
                           "message": "Backend connection lost — reconnecting…"}
    return board

def build_board(seed: int) -> dict:
    if _BRIDGE_MODE:
        return _bridge_board()
    pref = os.environ.get("DATA_SOURCE", "auto").lower()
    if pref != "demo" and LocalAuth.available():
        try:
            import live_match
            board = live_match.LiveMatch(LocalAuth()).build_scoreboard(
                include_stats=os.environ.get("LIVE_INCLUDE_STATS", "true").lower() != "false")
            board.setdefault("sourceDetail", "Local VALORANT client")
            return board
        except Exception as e:
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
    pass
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
    table.add_column("Agent", width=9, no_wrap=True)
    table.add_column("Name", width=15, no_wrap=True)
    table.add_column("Rank", width=12, no_wrap=True)
    table.add_column("RR", justify="right", width=3)
    table.add_column("Peak", width=12, no_wrap=True)
    table.add_column("Prev", width=9, no_wrap=True)
    table.add_column("LB", justify="right", width=5)
    table.add_column("HS%", justify="right", width=3)
    table.add_column("WR", justify="right", width=10, no_wrap=True)
    table.add_column("K/D", justify="right", width=4)
    table.add_column("Lvl", justify="right", width=5)

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

def main():
    ap = argparse.ArgumentParser(description="Valorant Scout terminal scoreboard")
    ap.add_argument("--once", action="store_true", help="print once and exit")
    ap.add_argument("--interval", type=float, default=5.0, help="refresh seconds")
    ap.add_argument("--seed", type=int, default=7, help="demo lobby seed")
    ap.add_argument("--bridge", action="store_true",
                    help="render the backend's board over the local WebSocket "
                         "bridge; never fetch from Riot directly")

    args, _ = ap.parse_known_args()

    if args.bridge:
        global _BRIDGE_MODE
        _BRIDGE_MODE = True
        threading.Thread(target=_bridge_loop, daemon=True,
                         name="scout-bridge").start()

    _set_window_title("Valorant Scout — Scoreboard")

    if args.once:
        console.print(render(build_board(args.seed)))
        return

    def board_key(board: dict) -> str:
        # `session` carries wall-clock timestamps that change every call; the
        # CLI doesn't render it, so keep it out of the change detection.
        slim = {k: v for k, v in board.items() if k != "session"}
        return json.dumps(slim, sort_keys=True, default=str)

    try:
        # We redraw on our own interval; auto_refresh would repaint the whole
        # screen ~4x/s (constant flicker in screen mode) for no benefit. And we
        # only repaint when the board actually CHANGED — a full-screen repaint
        # of identical content is visible flicker on legacy conhost (Windows
        # Sandbox, no Windows Terminal).
        board = build_board(args.seed)
        prev = board_key(board)
        with Live(render(board), console=console,
                  screen=True, transient=False, auto_refresh=False) as live:
            while True:
                time.sleep(max(1.0, args.interval))
                board = build_board(args.seed)
                key = board_key(board)
                if key != prev:
                    prev = key
                    live.update(render(board), refresh=True)
    except KeyboardInterrupt:
        console.print("[grey50]gg — bye.[/]")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        # The CLI runs in its own console that closes on crash — persist the
        # traceback so the failure is diagnosable afterwards.
        import traceback
        try:
            import scoutlog
            scoutlog.get_logger("cli").error("crashed:\n%s", traceback.format_exc())
        except Exception:
            pass
        raise
