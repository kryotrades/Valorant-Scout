"""
party_detector.py
=================
Detect recurring teammates ("party members") from a player's recent matches.

Algorithm (as specified):
  1. Walk every match and build a co-occurrence map counting how many of the
     subject's matches each other player appears in.
  2. Any teammate sharing >= PARTY_THRESHOLD matches is flagged a party member.
  3. Annotate each match with the party members present in it, and emit a
     ranked co-player summary (used by the front-end party graph).

This mirrors the spirit of the client's party-grouping (it colour-codes
players who queued together) but is computed from match history rather than the
live pre-game lobby.
"""

from __future__ import annotations

from collections import defaultdict

PARTY_THRESHOLD = 2


def build_cooccurrence(matches: list[dict]) -> dict[str, dict]:
    """puuid -> {puuid, name, sharedMatches, agents:set, matchIds:set}."""
    table: dict[str, dict] = {}
    for match in matches:
        for mate in match.get("teammates", []):
            puuid = mate.get("puuid")
            if not puuid:
                continue
            entry = table.get(puuid)
            if entry is None:
                entry = table[puuid] = {
                    "puuid": puuid,
                    "name": mate.get("name", "Unknown"),
                    "sharedMatches": 0,
                    "agents": set(),
                    "matchIds": set(),
                }
            mid = match.get("matchId")
            if mid not in entry["matchIds"]:
                entry["matchIds"].add(mid)
                entry["sharedMatches"] += 1
            if mate.get("agent"):
                entry["agents"].add(mate["agent"])
            # Keep the most recent display name we saw.
            entry["name"] = mate.get("name", entry["name"])
    return table


def analyze(matches: list[dict], top_n: int = 5) -> dict:
    """
    Returns:
      {
        "matches": [...same matches, each with `partyMembers`[]...],
        "coPlayers": [ {puuid, name, sharedMatches, agents[]} ...top_n... ],
        "partyCount": <distinct flagged party members>
      }
    """
    table = build_cooccurrence(matches)

    flagged = {
        puuid: e for puuid, e in table.items()
        if e["sharedMatches"] >= PARTY_THRESHOLD
    }

    # Annotate each match with which flagged party members appeared in it.
    annotated = []
    for match in matches:
        party_members = []
        for mate in match.get("teammates", []):
            puuid = mate.get("puuid")
            if puuid in flagged:
                party_members.append({
                    "puuid": puuid,
                    "name": mate.get("name", flagged[puuid]["name"]),
                    "agent": mate.get("agent"),
                    "sharedMatches": flagged[puuid]["sharedMatches"],
                })
        enriched = dict(match)
        enriched["partyMembers"] = party_members
        annotated.append(enriched)

    co_players = sorted(
        (
            {
                "puuid": e["puuid"],
                "name": e["name"],
                "sharedMatches": e["sharedMatches"],
                "agents": sorted(e["agents"]),
                "isParty": e["sharedMatches"] >= PARTY_THRESHOLD,
            }
            for e in table.values()
        ),
        key=lambda x: x["sharedMatches"],
        reverse=True,
    )[:top_n]

    return {
        "matches": annotated,
        "coPlayers": co_players,
        "partyCount": len(flagged),
    }
