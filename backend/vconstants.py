from __future__ import annotations

APP_VERSION = "1.1"

MAPS = [
    "Ascent", "Bind", "Haven", "Split", "Lotus", "Sunset",
    "Abyss", "Breeze", "Icebox", "Fracture", "Pearl", "Corrode", "Summit",
]

GAMEMODES = {
    "competitive": "Competitive",
    "unrated": "Unrated",
    "swiftplay": "Swiftplay",
    "spikerush": "Spike Rush",
    "deathmatch": "Deathmatch",
    "ggteam": "Escalation",
    "hurm": "Team Deathmatch",
    "fortcollins": "Retake",
    "skirmish2v2": "Skirmish 2v2",
    "custom": "Custom",
}

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
    pass
    if tier is None:
        tier = 0
    tier = max(0, min(int(tier), len(RANKS) - 1))
    return RANKS[tier]

def map_name_from_path(map_id: str) -> str:
    pass
    if not map_id:
        return "Unknown"
    leaf = map_id.rstrip("/").split("/")[-1]

    alias = {"Triad": "Haven", "Duality": "Bind", "Bonsai": "Split",
             "Ascent": "Ascent", "Port": "Icebox", "Foxtrot": "Breeze",
             "Canyon": "Fracture", "Pitt": "Pearl", "Jam": "Lotus",
             "Juliett": "Sunset", "Infinity": "Abyss", "Rook": "Corrode",
             "Plummet": "Summit",

             "HURM_Alley": "District", "HURM_Bowl": "Kasbah",
             "HURM_Helix": "Drift", "HURM_HighTide": "Glitch",
             "HURM_Yard": "Piazza",
             "Skirmish_A": "Skirmish A", "Skirmish_B": "Skirmish B",
             "Skirmish_C": "Skirmish C", "Skirmish_D": "Skirmish D",
             "Skirmish_E": "Skirmish E"}
    return alias.get(leaf, leaf if leaf in MAPS else (leaf or "Unknown"))

PARTY_COLORS = [
    "#E34343", "#D843E3", "#4346E3", "#43E3D0",
    "#5EE343", "#E2ED39", "#D452CF", "#E38F43",
]

def party_color(index: int) -> str:
    return PARTY_COLORS[index % len(PARTY_COLORS)]

STATES = {
    "MENUS": "In Lobby",
    "PREGAME": "Agent Select",
    "INGAME": "In Game",
    "OFFLINE": "Offline",
}
