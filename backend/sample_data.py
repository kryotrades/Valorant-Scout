from __future__ import annotations

import hashlib
import random
from datetime import datetime, timedelta, timezone

from agents import AGENTS
from vconstants import MAPS

_AGENT_NAMES = [a["name"] for a in AGENTS]

_NAME_PARTS_A = [
    "Toxic", "Silent", "Rapid", "Phantom", "Neon", "Vexed", "Frost", "Echo",
    "Lurk", "Solo", "Astra", "Viper", "Crimson", "Hazy", "Quick", "Zero",
    "Lucid", "Nova", "Karma", "Sage", "Drift", "Pixel", "Ghost", "Riot",
]
_NAME_PARTS_B = [
    "Diff", "Lock", "Aim", "Flick", "Smoke", "Clutch", "Dash", "Frag",
    "Wisp", "Ace", "Peek", "Spike", "Recon", "Bolt", "Edge", "Vibe",
    "Main", "Andy", "Gamer", "btw", "ttv", "yt", "Op", "Ent",
]
_TAGS = ["NA1", "EUW", "APAC", "1337", "GG", "VAL", "KR", "OCE", "000", "RR"]

def _seed_int(puuid: str) -> int:
    return int.from_bytes(hashlib.sha256(puuid.encode()).digest()[:8], "big")

def _make_name(rng: random.Random) -> str:
    return f"{rng.choice(_NAME_PARTS_A)}{rng.choice(_NAME_PARTS_B)}#{rng.choice(_TAGS)}"

def _fake_puuid(rng: random.Random) -> str:
    hexs = "0123456789abcdef"
    raw = "".join(rng.choice(hexs) for _ in range(32))
    return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"

def _line(rng: random.Random, rounds: int, won: bool, anchor: float):
    pass
    base_k = rng.uniform(0.55, 0.95) * rounds * (0.85 + anchor * 0.5)
    kills = max(3, int(base_k + rng.uniform(-3, 4)))
    deaths = max(3, int(rounds * rng.uniform(0.45, 0.8)))
    assists = max(0, int(rounds * rng.uniform(0.15, 0.5)))
    acs = int((kills * 150 + assists * 45 + rng.uniform(-40, 60) * rounds / 10) / max(rounds, 1) * (rounds / 24 + 0.6))
    acs = max(80, min(acs, 420))
    score = acs * rounds
    hs = round(rng.uniform(14, 44), 1)
    return {
        "kills": kills,
        "deaths": deaths,
        "assists": assists,
        "score": score,
        "acs": acs,
        "hsPct": hs,
    }

def generate_player(puuid: str, match_count: int = 20) -> dict:
    pass
    rng = random.Random(_seed_int(puuid))

    riot_id = _make_name(rng)
    skill = rng.uniform(0.25, 0.85)
    rank_tier = 3 + int(skill * 21)
    rr = rng.randint(8, 98)
    peak_tier = min(27, rank_tier + rng.randint(0, 3))

    main_agent = rng.choice(_AGENT_NAMES)
    secondary = rng.sample([a for a in _AGENT_NAMES if a != main_agent], 5)

    friends = [{"puuid": _fake_puuid(rng), "name": _make_name(rng)}
               for _ in range(rng.randint(2, 4))]

    now = datetime.now(timezone.utc)
    matches = []
    for i in range(match_count):
        mode = "Competitive" if rng.random() < 0.78 else rng.choice(["Unrated", "Swiftplay"])
        map_name = rng.choice(MAPS)
        won = rng.random() < (0.40 + skill * 0.30)
        if rng.random() < 0.04:
            result, rw, rl = "Draw", 12, 12
        elif won:
            result = "Victory"
            rw, rl = 13, rng.randint(3, 11)
        else:
            result = "Defeat"
            rw, rl = rng.randint(3, 11), 13
        rounds = rw + rl

        agent = main_agent if rng.random() < 0.45 else rng.choice(secondary)

        team = []
        n_friends = rng.randint(0, min(3, len(friends)))
        chosen_friends = rng.sample(friends, n_friends)
        for f in chosen_friends:
            team.append({
                "puuid": f["puuid"],
                "name": f["name"],
                "agent": rng.choice(_AGENT_NAMES),
            })
        while len(team) < 4:
            team.append({
                "puuid": _fake_puuid(rng),
                "name": _make_name(rng),
                "agent": rng.choice(_AGENT_NAMES),
            })

        matches.append({
            "matchId": _fake_puuid(rng),
            "map": map_name,
            "mode": mode,
            "date": (now - timedelta(hours=i * rng.uniform(5, 30))).isoformat(),
            "result": result,
            "roundsWon": rw,
            "roundsLost": rl,
            "agent": agent,
            "stats": _line(rng, rounds, won, skill),
            "teammates": team,
        })

    return {
        "puuid": puuid,
        "riotId": riot_id,
        "rankTier": rank_tier,
        "rr": rr,
        "peakTier": peak_tier,
        "matches": matches,
        "source": "demo",
    }
