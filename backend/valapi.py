from __future__ import annotations

import requests

BASE = "https://valorant-api.com/v1"
SKIN_SOCKET = "bcef87d6-209b-46c6-8b19-fbe40bd95abc"

WEAPON_UUIDS = {
    "Vandal": "9c82e19d-4575-0200-1a81-3eacf00cf872",
    "Phantom": "ee8e8d15-496b-07ac-e5f6-8fae5d4c7b1a",
    "Operator": "a03b24d3-4319-996d-0f8c-94bbfba1dfc7",
    "Sheriff": "e336c6b8-418d-9340-d77f-7a9e4cfe0702",
    "Classic": "29a0cfab-485b-f5d5-779a-b59f85e204a8",
}

_cache: dict = {}

def _get(path: str):
    if path in _cache:
        return _cache[path]
    try:
        data = requests.get(f"{BASE}/{path}", timeout=10).json().get("data")
    except Exception:
        data = None
    _cache[path] = data
    return data

def _skins_map() -> dict:
    if "_skins" in _cache:
        return _cache["_skins"]
    out = {}
    for skin in _get("weapons/skins") or []:
        out[skin["uuid"].lower()] = {
            "name": skin.get("displayName", "").strip(),
            "icon": skin.get("displayIcon"),
        }
    _cache["_skins"] = out
    return out

def skin_from_id(skin_id: str, weapon: str = "Vandal"):
    pass
    if not skin_id:
        return None
    entry = _skins_map().get(skin_id.lower())
    if not entry:
        return None
    name = entry["name"].replace(f" {weapon}", "").strip() or entry["name"]
    return {"name": name, "icon": entry["icon"]}

WEAPON_ORDER = [
    "Vandal", "Phantom", "Operator", "Sheriff", "Classic", "Ghost", "Frenzy",
    "Spectre", "Stinger", "Bulldog", "Guardian", "Marshal", "Outlaw", "Bucky",
    "Judge", "Ares", "Odin", "Shorty", "Melee",
]

def _weapons_map() -> dict:
    pass
    if "_weapons" in _cache:
        return _cache["_weapons"]
    out = {}
    for w in _get("weapons") or []:
        if w.get("uuid") and w.get("displayName"):
            out[w["uuid"].lower()] = {"name": w["displayName"],
                                      "icon": w.get("displayIcon")}
    _cache["_weapons"] = out
    return out

def weapon_name(uuid: str):
    if not uuid:
        return None
    entry = _weapons_map().get(uuid.lower())
    return entry["name"] if entry else None

def weapon_icon(uuid: str):
    pass
    if not uuid:
        return None
    entry = _weapons_map().get(uuid.lower())
    return entry["icon"] if entry else None

def skins_for_weapon(weapon: str) -> list:
    pass
    key = "_skinsfor_" + weapon.lower()
    if key in _cache:
        return _cache[key]
    suffix = " " + weapon.lower()
    out = []
    for v in _skins_map().values():
        n = v.get("name") or ""
        if v.get("icon") and n.lower().endswith(suffix) and not n.lower().startswith("standard"):
            out.append({"name": n[: -len(weapon) - 1].strip() or n, "icon": v["icon"]})
    _cache[key] = out
    return out

def loadout_weapons(items: dict) -> list:
    pass
    if not items:
        return []
    out = []
    for wuuid, item in items.items():
        wname = weapon_name(wuuid)
        if not wname:
            continue
        skin_id = (((item or {}).get("Sockets", {}) or {}).get(SKIN_SOCKET, {})
                   .get("Item", {}).get("ID"))
        skin = skin_from_id(skin_id, wname) if skin_id else None

        if (not skin or not skin.get("icon")
                or (skin.get("name") or "").strip().lower() == "standard"):
            skin = {"name": "Standard", "icon": weapon_icon(wuuid)}
        out.append({"weapon": wname, "skin": skin})
    order = {name: i for i, name in enumerate(WEAPON_ORDER)}
    out.sort(key=lambda w: order.get(w["weapon"], len(order)))
    return out

def _tiers_map() -> dict:
    if "_tiers" in _cache:
        return _cache["_tiers"]
    out = {}
    sets = _get("competitivetiers") or []
    if sets:
        for t in sets[-1].get("tiers", []):
            out[t["tier"]] = {"icon": t.get("smallIcon"), "large": t.get("largeIcon")}
    _cache["_tiers"] = out
    return out

def rank_icon(tier: int):
    return (_tiers_map().get(int(tier or 0)) or {}).get("icon")

def _titles_map() -> dict:
    if "_titles" in _cache:
        return _cache["_titles"]
    out = {t["uuid"].lower(): (t.get("titleText") or "")
           for t in (_get("playertitles") or [])}
    _cache["_titles"] = out
    return out

def title_text(title_id: str):
    if not title_id:
        return None
    return _titles_map().get(title_id.lower()) or None

def player_card(card_id: str, kind: str = "wide"):
    if not card_id:
        return None
    return f"https://media.valorant-api.com/playercards/{card_id}/{kind}art.png"

def _maps_map() -> dict:
    if "_maps" in _cache:
        return _cache["_maps"]
    out = {}
    for m in _get("maps") or []:
        if m.get("displayName"):
            out[m["displayName"]] = m.get("splash")
    _cache["_maps"] = out
    return out

def map_splash(name: str):
    if not name:
        return None
    return _maps_map().get(name)

def _act_number(name: str):
    pass
    parts = (name or "").strip().split()
    tok = parts[-1] if parts else ""
    if tok.isdigit():
        return int(tok)
    roman = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total = prev = 0
    for ch in reversed(tok.upper()):
        if ch not in roman:
            return None
        v = roman[ch]
        total += -v if v < prev else v
        prev = v
    return total or None

def _episode_label(name: str):
    pass
    if not name:
        return None
    for tok in name.split():
        if any(c.isalpha() for c in tok) and any(c.isdigit() for c in tok):
            return tok.upper()
    parts = name.strip().split()
    if parts and parts[0].upper() == "EPISODE":
        n = _act_number(name)
        return f"E{n}" if n else None
    return None

def _season_labels() -> dict:
    pass
    if "_seasonlabels" in _cache:
        return _cache["_seasonlabels"]
    data = _get("seasons") or []
    by_id = {s["uuid"].lower(): s for s in data if s.get("uuid")}
    out = {}
    for s in data:
        if "Act" not in (s.get("type") or ""):
            continue
        num = _act_number(s.get("displayName"))
        if num is None:
            continue
        ep = by_id.get((s.get("parentUuid") or "").lower())
        ep_label = _episode_label((ep or {}).get("displayName"))
        out[s["uuid"].lower()] = (f"{ep_label} Act {num}" if ep_label else f"Act {num}")
    _cache["_seasonlabels"] = out
    return out

def season_label(season_id: str):
    pass
    if not season_id:
        return None
    return _season_labels().get(season_id.lower())
