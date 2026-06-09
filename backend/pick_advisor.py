"""
pick_advisor.py
============
A "preferred agent" recommendation engine.

The idea: pin an agent to instalock, optionally
per map (`mapAgentSelect`). Here we invert that: instead of the user pinning an
agent, we *tally the agents they actually play* across recent matches and
recommend the one to instalock next — overall, per-map, and per-role — plus a
win-rate signal so the suggestion favours agents the player performs on.
"""

from __future__ import annotations

from collections import defaultdict

from agents import resolve_agent, role_of


def _blank_stat():
    return {"times": 0, "wins": 0, "kills": 0, "deaths": 0, "assists": 0}


def recommend(matches: list[dict]) -> dict:
    """
    Returns:
      {
        "agent": <name>, "agentId": <uuid>, "role": <role>,
        "times": <n>, "winRate": <0..100>, "portrait": <url>,
        "perMap":  { <map>: {agent, times} },
        "perRole": { <role>: {agent, times} },
        "breakdown": [ {agent, role, times, winRate, color, portrait} ... ]
      }
    """
    by_agent: dict[str, dict] = defaultdict(_blank_stat)
    by_map_agent: dict[str, dict] = defaultdict(lambda: defaultdict(int))
    by_role_agent: dict[str, dict] = defaultdict(lambda: defaultdict(int))

    for m in matches:
        agent = m.get("agent")
        if not agent:
            continue
        s = by_agent[agent]
        s["times"] += 1
        if m.get("result") == "Victory":
            s["wins"] += 1
        st = m.get("stats", {})
        s["kills"] += st.get("kills", 0)
        s["deaths"] += st.get("deaths", 0)
        s["assists"] += st.get("assists", 0)
        by_map_agent[m.get("map", "Unknown")][agent] += 1
        by_role_agent[role_of(agent)][agent] += 1

    if not by_agent:
        return {
            "agent": None, "agentId": None, "role": None, "times": 0,
            "winRate": 0, "portrait": None, "perMap": {}, "perRole": {},
            "breakdown": [],
        }

    def win_rate(stat):
        return round(100 * stat["wins"] / stat["times"], 1) if stat["times"] else 0.0

    # Rank by (times played, then win-rate) — most-played, best-performing.
    ranked = sorted(
        by_agent.items(),
        key=lambda kv: (kv[1]["times"], win_rate(kv[1])),
        reverse=True,
    )

    top_name, top_stat = ranked[0]
    top_agent = resolve_agent(top_name) or {}

    breakdown = []
    for name, stat in ranked:
        meta = resolve_agent(name) or {}
        kd = round(stat["kills"] / stat["deaths"], 2) if stat["deaths"] else float(stat["kills"])
        breakdown.append({
            "agent": name,
            "role": meta.get("role", "Flex"),
            "times": stat["times"],
            "winRate": win_rate(stat),
            "kd": kd,
            "color": meta.get("color", "#FF4655"),
            "portrait": meta.get("portrait"),
        })

    per_map = {
        mp: (lambda a: {"agent": a[0], "times": a[1]})(max(counts.items(), key=lambda kv: kv[1]))
        for mp, counts in by_map_agent.items()
    }
    per_role = {
        role: (lambda a: {"agent": a[0], "times": a[1]})(max(counts.items(), key=lambda kv: kv[1]))
        for role, counts in by_role_agent.items()
    }

    return {
        "agent": top_name,
        "agentId": top_agent.get("uuid"),
        "role": top_agent.get("role", "Flex"),
        "times": top_stat["times"],
        "winRate": win_rate(top_stat),
        "color": top_agent.get("color", "#FF4655"),
        "portrait": top_agent.get("portrait"),
        "fullPortrait": top_agent.get("fullPortrait"),
        "perMap": per_map,
        "perRole": per_role,
        "breakdown": breakdown,
    }
