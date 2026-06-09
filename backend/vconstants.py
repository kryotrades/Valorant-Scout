"""
vconstants.py
=============
Static VALORANT lookups shared across the backend.

`RANKS` is the tier ordering, `GAMEMODES` maps queue ids to names, and the map
list reflects the current competitive pool.
"""

from __future__ import annotations

# Competitive map pool (current rotation + recent).
MAPS = [
    "Ascent", "Bind", "Haven", "Split", "Lotus", "Sunset",
    "Abyss", "Breeze", "Icebox", "Fracture", "Pearl", "Corrode",
]

GAMEMODES = {
    "competitive": "Competitive",
    "unrated": "Unrated",
    "swiftplay": "Swiftplay",
    "spikerush": "Spike Rush",
    "deathmatch": "Deathmatch",
    "ggteam": "Escalation",
    "hurm": "Team Deathmatch",
    "custom": "Custom",
}

# Tier index (0..27) -> rank metadata. Order matches Riot's competitiveTier.
_RANK_GROUPS = [
    ("Unranked",  ["", "", ""],                       "#4A4A4A"),
    ("Iron",      ["Iron 1", "Iron 2", "Iron 3"],     "#5A5751"),
    ("Bronze",    ["Bronze 1", "Bronze 2", "Bronze 3"], "#BB8F5A"),
    ("Silver",    ["Silver 1", "Silver 2", "Silver 3"], "#AEB2B2"),
    ("Gold",      ["Gold 1", "Gold 2", "Gold 3"],     "#C5BA3F"),
    ("Platinum",  ["Platinum 1", "Platinum 2", "Platinum 3"], "#18A7B9"),
    ("Diamond",   ["Diamond 1", "Diamond 2", "Diamond 3"], "#D864C7"),
    ("Ascendant", ["Ascendant 1", "Ascendant 2", "Ascendant 3"], "#189452"),
    ("Immortal",  ["Immortal 1", "Immortal 2", "Immortal 3"], "#DD4444"),
    ("Radiant",   ["Radiant"],                        "#FFFDCD"),
]

RANKS: list[dict] = []
for _group, _names, _color in _RANK_GROUPS:
    for _n in _names:
        RANKS.append({
            "tier": len(RANKS),
            "name": _n or "Unranked",
            "group": _group,
            "color": _color,
        })


def rank_from_tier(tier: int | None) -> dict:
    """Return rank metadata for a competitiveTier int (clamped, null-safe)."""
    if tier is None:
        tier = 0
    tier = max(0, min(int(tier), len(RANKS) - 1))
    return RANKS[tier]


def map_name_from_path(map_id: str) -> str:
    """Decode a live val-match mapId path (e.g. '/Game/Maps/Ascent/Ascent')."""
    if not map_id:
        return "Unknown"
    leaf = map_id.rstrip("/").split("/")[-1]
    # A few internal codenames differ from display names.
    alias = {"Triad": "Haven", "Duality": "Bind", "Bonsai": "Split",
             "Ascent": "Ascent", "Port": "Icebox", "Foxtrot": "Breeze",
             "Canyon": "Fracture", "Pitt": "Pearl", "Jam": "Lotus",
             "Juliett": "Sunset", "Infinity": "Abyss", "Rook": "Corrode"}
    return alias.get(leaf, leaf if leaf in MAPS else (leaf or "Unknown"))


# Party highlight colours used to group players into parties.
PARTY_COLORS = [
    "#E34343", "#D843E3", "#4346E3", "#43E3D0",
    "#5EE343", "#E2ED39", "#D452CF", "#E38F43",
]


def party_color(index: int) -> str:
    return PARTY_COLORS[index % len(PARTY_COLORS)]


# Game flow states surfaced by the local presence API.
STATES = {
    "MENUS": "In Lobby",
    "PREGAME": "Agent Select",
    "INGAME": "In Game",
    "OFFLINE": "Offline",
}
