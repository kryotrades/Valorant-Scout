from __future__ import annotations

import random
import time

import sample_data
import valapi
from agents import AGENTS, resolve_agent
from live_match import (_career_summary, assemble_player, compute_smurf,
                        finalize, form_streak)
from vconstants import GAMEMODES, MAPS, party_color, rank_from_tier

_AGENT_NAMES = [a["name"] for a in AGENTS]
_A = ["Toxic", "Silent", "Rapid", "Neon", "Vexed", "Frost", "Echo", "Lurk",
      "Nova", "Karma", "Drift", "Pixel", "Ghost", "Riot", "Zen", "Vibe"]
_B = ["Diff", "Lock", "Aim", "Flick", "Clutch", "Frag", "Ace", "Peek",
      "Bolt", "Edge", "Main", "ttv", "Op", "Ent", "Wisp", "Dash"]
_TAGS = ["NA1", "EUW", "APAC", "1337", "GG", "VAL", "KR", "OCE"]
_SKINS = ["Prime", "Reaver", "Glitchpop", "Sovereign", "Elderflame", "Oni",
          "RGX 11z Pro", "Araxys", "Champions 2022", "Recon", "Ion",
          "Prelude to Chaos", "Sentinels of Light", "Gaia's Vengeance"]
_TITLES = ["Hardcore", "Mastermind", "Marksman", "Ace", "Clutch", "Tactician",
           "Legend", "Sharpshooter", "First Blood", "Rookie", "Vandalizer"]

_WEAPONS = ["Vandal", "Phantom", "Operator", "Sheriff", "Classic", "Ghost",
            "Spectre", "Marshal", "Guardian", "Judge", "Bulldog", "Odin"]

_CARDS = [
    "1711d20d-4b1c-c64a-14be-d4ae58a457c6", "c8b2f5fd-4331-b172-f3b7-c8a26f356a1f",
    "eef542d2-4724-bc47-f53f-239f8c9c2623", "d32e58b1-4191-7315-ad4a-9da58b3f23dd",
    "d2d3caf9-499f-2ac8-9722-54961c3bcbf5", "e8787c31-4a39-9636-94a5-77b298d26ba7",
]

_DEMO_QUEUE = {"queueId": "competitive", "inQueue": False, "queuedAt": None}
_DEMO_ELIGIBLE = [q for q in GAMEMODES if q != "custom"]

def demo_queue_state() -> dict:
    pass
    qid = _DEMO_QUEUE["queueId"]
    return {
        "available": True, "partyId": "demo-party",
        "queueId": qid, "queueName": GAMEMODES[qid],
        "eligible": [{"id": q, "name": GAMEMODES[q]} for q in _DEMO_ELIGIBLE],
        "state": "MATCHMAKING" if _DEMO_QUEUE["inQueue"] else "DEFAULT",
        "inQueue": _DEMO_QUEUE["inQueue"],
        "queuedAt": _DEMO_QUEUE["queuedAt"],
        "partySize": 1, "isOwner": True, "allReady": True,
        "demo": True,
    }

def demo_queue_set(queue_id: str) -> dict:
    _DEMO_QUEUE["queueId"] = queue_id
    return {"ok": True, "status": "demo", "queueId": queue_id,
            "message": f"Gamemode set to {GAMEMODES[queue_id]} (demo)."}

def demo_queue_start() -> dict:
    _DEMO_QUEUE.update(inQueue=True, queuedAt=time.time())
    return {"ok": True, "status": "demo", "inQueue": True,
            "message": f"Queue started — {GAMEMODES[_DEMO_QUEUE['queueId']]} (demo)."}

def demo_queue_stop() -> dict:
    _DEMO_QUEUE.update(inQueue=False, queuedAt=None)
    return {"ok": True, "status": "demo", "inQueue": False,
            "message": "Queue cancelled (demo)."}

def _weapons(rng):
    pass
    out = []
    for w in _WEAPONS:
        choices = valapi.skins_for_weapon(w)
        if choices:
            out.append({"weapon": w, "skin": rng.choice(choices)})
        else:
            out.append({"weapon": w, "skin": {"name": rng.choice(_SKINS), "icon": None}})
    return out

def _intel(rng, main_agent=None, map_name=None, hot=False):
    pass
    others = [a for a in rng.sample(_AGENT_NAMES, 4) if a != main_agent][:2]
    main = main_agent or others.pop(0)
    top = [{"agent": main, "games": rng.randint(3, 6)}]
    top += [{"agent": a, "games": rng.randint(1, 3)} for a in others]
    if hot:
        form = ["W"] * rng.randint(3, 4)
        form += [rng.choice(["W", "L"]) for _ in range(5 - len(form))]
    else:
        form = [rng.choice(["W", "L"]) for _ in range(5)]
    map_wins = {}
    if map_name:
        g = rng.randint(2, 6)
        map_wins[map_name] = [rng.randint(0, g), g]
    return {"topAgents": top, "form": form, "streak": form_streak(form),
            "mapWins": map_wins}

def _name(rng):
    return f"{rng.choice(_A)}{rng.choice(_B)}#{rng.choice(_TAGS)}"

def _puuid(rng):
    h = "0123456789abcdef"
    s = "".join(rng.choice(h) for _ in range(32))
    return f"{s[:8]}-{s[8:12]}-{s[12:16]}-{s[16:20]}-{s[20:]}"

def generate(seed: int = 7) -> dict:
    rng = random.Random(seed)

    agents = rng.sample(_AGENT_NAMES, 10)
    lobby_tier = rng.randint(11, 24)
    map_name = rng.choice(MAPS)

    party_specs = [("Blue", [0, 1, 2]), ("Red", [0, 1])]
    party_lookup = {}
    parties_out = []

    raw = {"Blue": [], "Red": []}
    self_index = 0
    for team in ("Blue", "Red"):
        for i in range(5):
            raw[team].append({
                "puuid": _puuid(rng),
                "name": _name(rng),
                "agent": agents.pop(),
                "hiddenName": rng.random() < 0.35,
                "hiddenLevel": rng.random() < 0.2,
            })

    self_puuid = raw["Blue"][self_index]["puuid"]

    for idx, (team, members) in enumerate(party_specs):
        color = party_color(idx)
        pid = _puuid(rng)
        puuids = [raw[team][i]["puuid"] for i in members]
        parties_out.append({"id": pid, "color": color, "number": idx + 1,
                            "size": len(puuids), "members": puuids})
        for pu in puuids:
            party_lookup[pu] = {"id": pid, "color": color, "number": idx + 1}

    smurf_slots = {("Blue", 1), ("Red", 1)}

    players = []
    for team in ("Blue", "Red"):
        for i, slot in enumerate(raw[team]):
            tier = max(3, min(27, lobby_tier + rng.randint(-3, 3)))
            peak = min(27, tier + rng.randint(0, 4))
            lb = rng.randint(1, 500) if tier >= 24 else 0
            games = rng.randint(20, 400)
            win_rate = rng.randint(38, 64)
            kd = round(rng.uniform(0.6, 1.8), 2)
            level = rng.randint(20, 480)
            if (team, i) in smurf_slots:
                peak = max(peak, 22)
                tier = max(tier, 20)
                kd = round(rng.uniform(1.4, 2.1), 2)
                win_rate = max(win_rate, 64)
                games = max(games, 20)
                level = rng.randint(18, 55)
            weapons = _weapons(rng)
            smurf, smurf_reasons = compute_smurf(
                level=level, peak_tier=peak, rank_tier=tier,
                kd=kd, win_rate=win_rate, games=games)
            players.append(assemble_player(
                puuid=slot["puuid"],
                name=slot["name"],
                name_hidden=slot["hiddenName"],
                team=team,
                is_self=(slot["puuid"] == self_puuid),
                agent_id=slot["agent"],
                rank_tier=tier,
                rr=rng.randint(0, 99),
                leaderboard=lb,
                peak_tier=peak,
                prev_tier=max(0, tier - rng.randint(0, 3)),
                win_rate=win_rate,
                games=games,
                kd=kd,
                hs=rng.randint(12, 34),
                level=level,
                level_hidden=slot["hiddenLevel"],
                party=party_lookup.get(slot["puuid"]),
                skin=next(w["skin"] for w in weapons if w["weapon"] == "Vandal"),
                weapons=weapons,
                peak_act=f"V{rng.randint(25, 26)} Act {rng.randint(1, 5)}",
                rr_earned=rng.randint(-24, 28),
                title=rng.choice(_TITLES),
                player_card=valapi.player_card(rng.choice(_CARDS)),
                smurf=smurf, smurf_reasons=smurf_reasons,
                intel=_intel(rng, slot["agent"], map_name,
                             hot=(team, i) in smurf_slots),
            ))

    ally, enemy = sorted([rng.randint(0, 13), rng.randint(0, 13)], reverse=True)
    board = finalize(players, state="INGAME", source="demo", self_team="Blue",
                     map_name=map_name, queue="competitive",
                     match_id=f"demo-{seed}", parties=parties_out,
                     map_splash=valapi.map_splash(map_name),
                     score={"ally": ally, "enemy": enemy, "round": ally + enemy + 1})
    board["sourceDetail"] = "Demo lobby (open VALORANT for live data)"

    board["queue"] = demo_queue_state()
    board["session"] = session(seed)
    return board

def generate_lobby(seed: int = 7) -> dict:
    pass
    rng = random.Random(seed * 31 + 5)
    size = rng.randint(2, 5)
    tier = rng.randint(11, 24)
    puuids = [_puuid(rng) for _ in range(size)]
    party = {"id": "lobby", "color": party_color(0), "number": 1, "size": size}

    players = []
    for i in range(size):
        t = max(3, min(27, tier + rng.randint(-3, 3)))
        peak = min(27, t + rng.randint(0, 4))
        win_rate = rng.randint(38, 64)
        games = rng.randint(20, 400)
        kd = round(rng.uniform(0.6, 1.8), 2)
        level = rng.randint(20, 480)
        if i == 1:
            peak = max(peak, 22)
            t = max(t, 20)
            kd = round(rng.uniform(1.4, 2.1), 2)
            level = rng.randint(18, 55)
        smurf, smurf_reasons = compute_smurf(
            level=level, peak_tier=peak, rank_tier=t,
            kd=kd, win_rate=win_rate, games=games)
        players.append(assemble_player(
            puuid=puuids[i], name=_name(rng), name_hidden=False, team="Blue",
            is_self=(i == 0), agent_id="",
            rank_tier=t, rr=rng.randint(0, 99), leaderboard=0,
            peak_tier=peak, prev_tier=max(0, t - rng.randint(0, 3)),
            win_rate=win_rate, games=games,
            kd=kd, hs=rng.randint(12, 34),
            level=level, level_hidden=False,
            party=party if size > 1 else None,
            peak_act=f"V{rng.randint(25, 26)} Act {rng.randint(1, 5)}",
            title=rng.choice(_TITLES),
            player_card=valapi.player_card(rng.choice(_CARDS)),
            smurf=smurf, smurf_reasons=smurf_reasons,
            intel=_intel(rng, hot=(i == 1)),
        ))

    parties_out = [{**party, "members": puuids}] if size > 1 else []
    board = finalize(players, state="MENUS", source="demo", self_team="Blue",
                     map_name=None, queue="Lobby", match_id=f"demo-lobby-{seed}",
                     parties=parties_out)
    board["sourceDetail"] = "Demo lobby (open VALORANT for live data)"
    board["queue"] = demo_queue_state()
    board["session"] = session(seed)
    return board

def match_detail(match_id: str, subject: str = None) -> dict:
    pass
    rng = random.Random(sum(ord(c) for c in (match_id or "x")) + 11)
    agents = rng.sample(_AGENT_NAMES, 10)
    map_name = rng.choice(MAPS)
    blue_won = rng.random() < 0.5
    rw, rl = (13, rng.randint(3, 11)) if blue_won else (rng.randint(3, 11), 13)

    players = []
    for i in range(10):
        agent = resolve_agent(agents[i]) or {}
        is_subject = bool(subject and i == 0)
        k, d, a = rng.randint(8, 30), rng.randint(8, 24), rng.randint(2, 12)
        players.append({
            "puuid": subject if is_subject else _puuid(rng),
            "name": "This player" if is_subject else _name(rng),
            "team": "Blue" if i < 5 else "Red",
            "agent": agents[i],
            "agentPortrait": agent.get("portrait"),
            "agentColor": agent.get("color", "#8B978F"),
            "kills": k, "deaths": d, "assists": a,
            "kd": round(k / d, 2) if d else float(k),
            "acs": rng.randint(120, 320),
            "hsPct": rng.randint(12, 40),
            "isSubject": is_subject,
        })
    players.sort(key=lambda x: -x["acs"])
    return {
        "matchId": match_id, "map": map_name, "mode": "Competitive",
        "scores": {"Blue": rw, "Red": rl},
        "result": ("Victory" if blue_won else "Defeat") if subject else None,
        "players": players,
    }

def recap(seed: int = 7) -> dict:
    pass
    rng = random.Random(seed * 17 + 3)
    detail = match_detail(f"demo-{seed}", subject="demo-self")
    players = detail["players"]
    you = next(p for p in players if p["isSubject"])
    mvp = players[0]
    team_mvp = next(p for p in players if p["team"] == you["team"])
    won = detail["result"] == "Victory"
    delta = rng.randint(10, 28) * (1 if won else -1)
    return {
        "matchId": detail["matchId"],
        "map": detail["map"], "mode": detail["mode"],
        "result": detail["result"], "scores": detail["scores"],
        "mvp": mvp, "teamMvp": team_mvp if team_mvp is not mvp else None,
        "you": you, "yourAvgKd": round(rng.uniform(0.8, 1.4), 2),
        "rrDelta": delta, "tierAfter": rng.randint(11, 24),
        "rrAfter": rng.randint(0, 99),
        "at": int(time.time()), "demo": True,
    }

def session(seed: int = 7) -> dict:
    pass
    rng = random.Random(seed * 13 + 1)
    n = rng.randint(5, 9)
    now = int(time.time())
    rr, tier = rng.randint(20, 80), rng.randint(11, 24)
    points = []
    for i in range(n):
        delta = rng.choice([1, 1, -1]) * rng.randint(12, 28)
        rr += delta
        if rr >= 100:
            rr -= 100
            tier = min(27, tier + 1)
        elif rr < 0:
            rr += 100
            tier = max(3, tier - 1)
        points.append({"matchId": f"demo-s{i}", "ts": now - (n - i) * 2400,
                       "map": rng.choice(MAPS),
                       "result": "Victory" if delta > 0 else "Defeat",
                       "delta": delta, "tier": tier, "rr": rr})
    return {"startedAt": points[0]["ts"],
            "net": sum(p["delta"] for p in points), "points": points}

def encounters(seed: int = 7) -> list:
    pass
    rng = random.Random(seed * 7 + 9)
    now = int(time.time())
    out = []
    for i in range(12):
        ww, lw = rng.randint(0, 3), rng.randint(0, 3)
        wa, la = rng.randint(0, 3), rng.randint(0, 5)
        tier = rng.randint(8, 24)
        out.append({
            "puuid": _puuid(rng), "name": _name(rng),
            "withCount": ww + lw, "againstCount": wa + la,
            "winsWith": ww, "lossesWith": lw,
            "winsAgainst": wa, "lossesAgainst": la,
            "rank": rank_from_tier(tier)["name"],
            "peakRank": rank_from_tier(min(27, tier + rng.randint(0, 3)))["name"],
            "kd": round(rng.uniform(0.7, 1.7), 2),
            "winRate": rng.randint(38, 64),
            "level": rng.randint(20, 400),
            "lastSeen": now - rng.randint(1, 14) * 86400,
            "agents": rng.sample(_AGENT_NAMES, rng.randint(1, 3)),
        })
    return out

def career(puuid: str) -> dict:
    pass
    raw = sample_data.generate_player(puuid, match_count=10)
    matches = []
    for i, m in enumerate(raw["matches"]):
        st = m["stats"]
        agent = resolve_agent(m["agent"]) or {}
        matches.append({
            "matchId": m["matchId"],
            "map": m["map"],
            "mode": m["mode"],
            "startMillis": i,
            "result": m["result"],
            "agent": m["agent"],
            "agentPortrait": agent.get("portrait"),
            "agentColor": agent.get("color", "#8B978F"),
            "kills": st["kills"],
            "deaths": st["deaths"],
            "assists": st["assists"],
            "kd": round(st["kills"] / st["deaths"], 2) if st["deaths"] else float(st["kills"]),
            "acs": st["acs"],
            "hsPct": round(st["hsPct"]),
            "teammates": [{"puuid": t["puuid"], "name": t["name"], "agent": t["agent"]}
                          for t in m["teammates"]],
        })
    return {"source": "demo", "puuid": puuid, "matches": matches,
            **_career_summary(matches)}
