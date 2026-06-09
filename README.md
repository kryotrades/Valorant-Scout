<div align="center">

![Valorant Scout](docs/banner.svg)

# Valorant Scout

**Live in-match intelligence for VALORANT — see every player's rank, peak, K/D, win-rate, smurf risk, full skin inventory and party before the round even starts.**

Reads your **local VALORANT client** in real time and renders it as a slick web dashboard, a colour-coded terminal scoreboard, and your **Discord status** — plus an instalock helper, a cross-session encounter log, and a VALORANT chat **ASCII art studio**.

[Features](#-features) · [Quick start](#-quick-start) · [Screens](#-screens) · [CLI](#-terminal-cli) · [Discord](#-discord-rich-presence) · [Config](#-configuration) · [Credits](#-credits)

`VALORANT` · `rank tracker` · `live scoreboard` · `smurf detector` · `instalock` · `incognito name reveal` · `discord rich presence` · `ascii chat art` · `Flask` · `Next.js` · `Rich CLI`

</div>

---

## ✨ Features

### 🎯 Live in-match scoreboard
- **Every player, both teams** — pulled live from the local client the moment you hit Agent Select or load in.
- **Rank, RR & leaderboard place**, current act + **peak rank with the act it was hit** (`V26 Act 1`), and previous-act rank.
- **K/D and HS%** — competitive-aware: in a ranked game these aggregate the player's **last 5 competitive matches**; in other modes, their most recent game.
- **Win-rate & games** for the active act.
- **Account level** (recovered from match history even when the client hides it).
- **Party detection** — colour-coded groups so you instantly see the 3-stack on the enemy team.
- **Per-team averages** — avg rank, avg K/D, avg WR, smurf count, and a live **win-probability** bar.

### 🕵️ Smurf radar
Heuristic flags that combine **account level vs. peak rank**, **K/D**, and **win-rate** to surface likely smurfs — with the reasons shown on hover.

### 👤 Deep player profiles
Click any player for a full drill-in:
- **Recent form** — K/D, win%, HS%, K/D/A averages.
- **Full weapon skin inventory** with real skin art (Standard guns show the base weapon render).
- **Past games** — agent, map, result, K/D/A, ACS, HS% — click into any match for the full scoreboard.
- **Encounters** — how many times you've queued **with or against** this player across sessions.
- One-click **tracker.gg** deep link.

### 🔓 Hidden-name handling
Resolves Incognito ("hidden") names where the client allows it, and never renders a bare `#` — unknowns fall back to a clean `Player-XXXX` tag.

### ⚡ Instalock & agent-select tools
- **Auto-instalock** that loops until your agent is locked (or you hit stop), with **per-map agent presets**.
- **Check side** (attack/defend) and **dodge** buttons.
- **Region selector** (NA / EU / AP / KR / LATAM / BR) with auto-detect.
- **Dry-run by default** — you explicitly opt in before anything touches the client.

### 🗂️ Encounter log
A local JSON ledger of everyone you've played with or against, with play counts and their latest stats — so "haven't I seen this Jett before?" finally has an answer.

### 🎮 Discord Rich Presence
Shows your VALORANT status on your Discord profile: **map, mode, rank, agent, side and live score** across lobby / agent-select / in-game.

### ⌨️ Terminal CLI
A fast, colour-coded `rich` scoreboard for a second monitor — same data, no browser.

### 🖌️ VALORANT ASCII studio
- A searchable **gallery** of chat-ready ASCII art across categories (Animals, Cute, Emojis, Funny, Texts, …).
- A **creator** with a text→banner generator and a paintable draw grid.
- Output is encoded the way VALORANT chat actually needs it (visible background fill, fixed-width rows, single-space joins) so the art survives the paste.

### 🎨 Built to look good
Cinematic dark UI, Framer-Motion animation, big readable type, agent splash art, and a reduced-motion-friendly fallback.

---

## 🚀 Quick start (Windows — no coding needed)

1. **Download** this repo (green **Code → Download ZIP**) and unzip it anywhere.
2. Double-click **`install.bat`** — one-time setup. It installs the right Python if you don't have
   it, sets everything up, asks you to **pick your region**, and drops a **Valorant Scout** shortcut
   on your Desktop (drag it to your taskbar to pin it).
3. Double-click **`start.bat`** (or the Desktop shortcut) any time to launch. It **auto-updates** to
   the newest release, then opens the **live dashboard** in your browser.
   - `UPDATE.bat` updates manually whenever you want.

The dashboard is served from the web and connects **securely back to the app running on your PC** —
your match data never leaves your machine. The first time, your browser may ask to **allow access to
your local network** — click **Allow** (Chrome or Edge recommended). **“View on phone”** then works
out of the box — scan the QR to control Scout from your phone.

That's it — **no `.env` or config to edit, and no Node.js build.** With VALORANT closed it runs in a
fully-populated **demo mode**, so you can explore the UI any time.

> Windows SmartScreen may warn about a downloaded `.bat` — choose *More info → Run anyway*.

### Developers / manual run
```bash
pip install -r backend/requirements.txt
python run.py            # backend + terminal scoreboard, against the hosted dashboard
python run.py --no-cli   # backend only (no terminal window)
python run.py --cli      # terminal scoreboard only
```
By default the app opens the hosted dashboard; point it elsewhere with `FRONTEND_URL` in
`backend/.env`. The full source (Flask backend **+** Next.js frontend) lives in the development repo
if you want to run or modify the website locally.

---

## 🖼️ Screens

### Live scoreboard
![Live scoreboard](docs/screenshots/scoreboard.png)

### Player profile
![Player profile](docs/screenshots/profile.png)

### ASCII chat-art studio — gallery & creator
![ASCII gallery](docs/screenshots/ascii-gallery.png)
![ASCII creator](docs/screenshots/ascii-creator.png)

### Terminal CLI
![Terminal scoreboard](docs/screenshots/cli.svg)

### Discord Rich Presence
![Discord presence](docs/screenshots/discord.png)

---

## ⌨️ Terminal CLI

```bash
python cli.py                 # live table, refreshes every 5s
python cli.py --once          # print once and exit
python cli.py --interval 3    # custom refresh seconds
python cli.py --seed 12       # pick a demo lobby
```

Columns: Party · Agent · Name · Rank · RR · Peak (with act) · Previous · Leaderboard · HS% · Win-rate · K/D · Level.

---

## 🎮 Discord Rich Presence

Enabled by default — just have the **Discord desktop app** running. It updates every ~15s with your map, mode, rank, agent, side and live score. Disable it with `DISCORD_RPC=false` in your `.env`.

---

## ⚙️ Configuration

Copy `backend/.env.example` to `backend/.env`. **Everything is optional** — the app runs in demo mode with nothing set, and when VALORANT is open your PUUID is detected automatically from the running client (no need to enter it).

| Key | What it does |
|-----|--------------|
| `RIOT_API_KEY` | Optional official Riot API key (improves name resolution). |
| `RIOT_REGION` | Pin your region (`na`, `eu`, `ap`, `kr`, `latam`, `br`) instead of auto-detect. |
| `DATA_SOURCE` | `auto` (live if the client is running, else demo), `live`, or `demo`. |
| `DISCORD_RPC` | `true` / `false` — toggle Discord Rich Presence. |
| `ALLOW_LIVE_INSTALOCK` | `true` / `false` — allow real (non-dry-run) instalock/dodge. |

---

## 🏗️ How it works

```
VALORANT local client  ──►  Flask API (backend/)  ──►  Next.js dashboard (frontend/)
   lockfile + edge APIs        live_match pipeline         React + Tailwind + Motion
                                    │  └─►  Rich terminal CLI (cli.py)
                                    └─►  Discord Rich Presence (pypresence)
```

- **`backend/`** — Flask service: live scoreboard pipeline, rank/stat resolution, party detection, encounter log, instalock worker, Discord presence. Art metadata is resolved from the public [valorant-api.com](https://valorant-api.com) CDN, so no binary assets are bundled.
- **`frontend/`** — Next.js (pages router) + Tailwind + Framer Motion.
- **`cli.py`** — standalone terminal scoreboard.
- **`run.py`** — one-command launcher for the whole stack.

---

## 🙏 Credits

Valorant Scout stands on the shoulders of the community projects that mapped out the local client and inspired these features:

- **[VALORANT-rank-yoinker](https://github.com/zayKenyon/VALORANT-rank-yoinker)** — the live scoreboard / rank pipeline and Discord presence approach.
- **[Fast-Pick](https://github.com/Imu-D-sama/Fast-Pick)** — the instalock, check-side and dodge flow, and region handling.
- **[ValForge](https://valforge.gg/ascii)** — the community, free-to-use, open-source ASCII gallery that seeds the chat art studio (credit to its individual art creators).
- **[valorant-api.com](https://valorant-api.com)** — agent / weapon / rank / map / season art and metadata.

Huge thanks to all of them. ❤️

---

## 🔄 Updating

`start.bat` checks for a newer GitHub release on every launch and updates automatically
(`UPDATE.bat` does it on demand). Updates **preserve** your settings, data and installed packages,
and only re-install dependencies that actually changed.

**Cutting a release (maintainers):** bump the root `VERSION` file, commit, then publish a GitHub
Release whose tag matches (e.g. `v1.1.0`). Clients on an older `VERSION` pick it up on next launch.

## 📜 License

Licensed under the **GNU General Public License v3.0** — see [`LICENSE`](LICENSE). © 2026 kryotrades.

## ⚠️ Disclaimer

Valorant Scout is a third-party tool and is **not affiliated with, endorsed by, or sponsored by Riot Games**. It reads the local client's APIs and can automate parts of agent select; **client automation may violate Riot's Terms of Service** and is provided for educational use. Instalock / dodge are **dry-run by default** — you opt in at your own risk.
