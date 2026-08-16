from __future__ import annotations


from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
import json
import os
import tempfile
import threading
import time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import valapi
from vconstants import map_name_from_path, rank_from_tier

_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
_PATH = os.path.join(_DATA_DIR, "rr_history.json")
_LOCK = threading.RLock()
_MAX_POINTS = 2000
_REFRESH_TTL = 600.0
_MIN_TOTAL = 20
_MIN_BUCKET = 8
_refresh_at: dict[str, float] = {}
_enrich_at: dict[str, float] = {}


def _empty_store() -> dict:
    return {"version": 3, "accounts": {}, "discardedOrphanPoints": 0}


def _normalise_store(raw) -> dict:
    if not isinstance(raw, dict):
        return _empty_store()
    version = raw.get("version")
    if version == 3 and isinstance(raw.get("accounts"), dict):
        out = _empty_store()
        out.update(raw)
        return out
    if version == 2 and isinstance(raw.get("accounts"), dict):
        out = _empty_store()
        discarded = len(raw.get("legacyPoints") or [])
        for puuid, account in raw["accounts"].items():
            if not puuid or not isinstance(account, dict):
                continue
            points = account.get("points") or []
            kept = [p for p in points if isinstance(p, dict) and p.get("source") != "legacy"]
            discarded += len(points) - len(kept)
            out["accounts"][puuid] = {**account, "points": kept}
        out["discardedOrphanPoints"] = discarded
        return out
    out = _empty_store()
    out["discardedOrphanPoints"] = len(raw.get("points") or [])
    return out


def _load() -> dict:
    try:
        with open(_PATH, encoding="utf-8") as fh:
            return _normalise_store(json.load(fh))
    except Exception:
        return _empty_store()


def _save() -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=_DATA_DIR, prefix=".rrhist-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(_STORE, fh, separators=(",", ":"))
            os.replace(tmp, _PATH)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
    except Exception:
        pass


_STORE = _load()

try:
    with open(_PATH, encoding="utf-8") as _history_file:
        _stored_version = json.load(_history_file).get("version")
except (OSError, ValueError, AttributeError):
    _stored_version = 3
if _stored_version != 3:
    _save()


def _valid_timezone(name: str | None) -> tuple[str, ZoneInfo]:
    candidate = (name or "").strip()
    if candidate:
        try:
            return candidate, ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            pass
    return "UTC", ZoneInfo("UTC")


def _quality(point: dict) -> int:
    score = 10 if point.get("source") == "live" else 1
    score += sum(point.get(k) is not None for k in ("result", "delta", "tier", "rr"))
    if point.get("resultExact"):
        score += 3
    return score


def _clean_point(point: dict, source: str) -> dict:
    allowed = (
        "matchId", "ts", "map", "result", "delta", "tier", "rr",
        "seasonId", "actId", "agent", "agentPortrait", "agentColor",
        "partySize", "mode", "scores", "kills", "deaths", "assists",
        "kd", "acs", "hsPct", "resultExact",
    )
    out = {k: point.get(k) for k in allowed if point.get(k) is not None}
    out["source"] = source
    if "resultExact" not in out:
        out["resultExact"] = source == "live"
    return out


def _upsert_points(points: list[dict], point: dict, source: str) -> list[dict]:
    cleaned = _clean_point(point, source)
    match_id = cleaned.get("matchId")
    if not match_id or not cleaned.get("ts"):
        return points
    existing = next((p for p in points if p.get("matchId") == match_id), None)
    if existing is None:
        points.append(cleaned)
    else:
        merged = dict(existing)
        for key, value in cleaned.items():
            if value is None:
                continue
            if _quality(cleaned) >= _quality(existing) or merged.get(key) is None:
                merged[key] = value
        existing.clear()
        existing.update(merged)
    deduped = {p.get("matchId"): p for p in points if p.get("matchId") and p.get("ts")}
    return sorted(deduped.values(), key=lambda p: p.get("ts") or 0)[-_MAX_POINTS:]


def _ensure_account(puuid: str, riot_id: str | None = None,
                    timezone_name: str | None = None) -> dict:
    accounts = _STORE.setdefault("accounts", {})
    account = accounts.setdefault(puuid, {"points": []})
    if riot_id:
        account["riotId"] = riot_id
    if timezone_name:
        account["timezone"] = _valid_timezone(timezone_name)[0]
    account["lastSeenAt"] = int(time.time())

    return account


def record(point: dict, puuid: str | None = None, riot_id: str | None = None,
           timezone_name: str | None = None, source: str = "live") -> None:
    owner = puuid or point.get("puuid")
    if not owner or str(owner).startswith("demo"):
        return
    with _LOCK:
        account = _ensure_account(str(owner), riot_id or point.get("riotId"), timezone_name)
        account["points"] = _upsert_points(account.get("points", []), point, source)
        _save()


def refresh(auth, timezone_name: str | None = None) -> str | None:
    try:
        auth.headers()
        puuid = auth.puuid
    except Exception:
        return None
    if not puuid:
        return None

    now = time.time()
    with _LOCK:
        account = _ensure_account(puuid, timezone_name=timezone_name)
        if now - _refresh_at.get(puuid, 0) < _REFRESH_TTL:
            _save()
            return puuid
        _refresh_at[puuid] = now
    try:
        matches = []
        for start in (0, 20):
            cu = auth.pd_get(
                f"/mmr/v1/players/{puuid}/competitiveupdates"
                f"?startIndex={start}&endIndex={start + 20}&queue=competitive")
            if not isinstance(cu, dict) or cu.get("errorCode"):
                code = cu.get("errorCode", "MMR_REQUEST_FAILED") if isinstance(cu, dict) else "MMR_REQUEST_FAILED"
                raise RuntimeError(code)
            page = cu.get("Matches") or []
            matches.extend(page)
            if len(page) < 20:
                break
        with _LOCK:
            account = _ensure_account(puuid, timezone_name=timezone_name)
            points = account.get("points", [])
            for match in matches:
                delta = match.get("RankedRatingEarned")
                ts = int((match.get("MatchStartTime") or 0) / 1000) or None
                if not match.get("MatchID") or not ts:
                    continue
                result = None
                if isinstance(delta, (int, float)) and delta:
                    result = "Victory" if delta > 0 else "Defeat"
                point = {
                    "matchId": match.get("MatchID"),
                    "ts": ts,
                    "map": map_name_from_path(match.get("MapID") or ""),
                    "result": result,
                    "resultExact": False,
                    "delta": delta,
                    "tier": match.get("TierAfterUpdate"),
                    "rr": match.get("RankedRatingAfterUpdate"),
                    "seasonId": match.get("SeasonID") or match.get("SeasonId"),
                }
                points = _upsert_points(points, point, "backfill")
            account["points"] = points
            account["lastBackfillAt"] = int(time.time())
            account["backfillMatches"] = len(matches)
            account.pop("backfillError", None)
            _save()
    except Exception:
        with _LOCK:
            _refresh_at.pop(puuid, None)
            account = _ensure_account(puuid, timezone_name=timezone_name)
            account["backfillError"] = True
            _save()
    return puuid


def enrich(live_match, puuid: str, limit: int = 20) -> int:
    limit = max(0, min(20, int(limit or 0)))
    if not puuid or not limit:
        return 0
    now = time.time()
    with _LOCK:
        if now - _enrich_at.get(puuid, 0) < _REFRESH_TTL:
            account = _ensure_account(puuid)
            return sum(p.get("acs") is not None for p in account.get("points", [])[-limit:])
        _enrich_at[puuid] = now
        account = _ensure_account(puuid)
        candidates = [p for p in account.get("points", [])[-limit:]
                      if p.get("matchId") and (p.get("acs") is None
                                               or p.get("scores") is None
                                               or p.get("partySize") is None)]

    def fetch(point):
        try:
            detail = live_match.auth.pd_get(f"/match-details/v1/matches/{point['matchId']}")
            row = live_match._career_match(detail, puuid, point["matchId"])
            return row
        except Exception:
            return None

    rows = []
    if candidates:
        with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as pool:
            rows = [row for row in pool.map(fetch, candidates) if row]
        for row in rows:
            record({
                "matchId": row.get("matchId"),
                "ts": int((row.get("startMillis") or 0) / 1000)
                      or next((p.get("ts") for p in candidates if p.get("matchId") == row.get("matchId")), None),
                "map": row.get("map"), "mode": row.get("mode"),
                "result": row.get("result"), "agent": row.get("agent"),
                "agentPortrait": row.get("agentPortrait"), "agentColor": row.get("agentColor"),
                "kills": row.get("kills"), "deaths": row.get("deaths"),
                "assists": row.get("assists"), "kd": row.get("kd"),
                "acs": row.get("acs"), "hsPct": row.get("hsPct"),
                "partySize": row.get("partySize"), "scores": row.get("scores"),
            }, puuid=puuid, source="live")
    with _LOCK:
        account = _ensure_account(puuid)
        if candidates and not rows:
            _enrich_at.pop(puuid, None)
            account["enrichError"] = True
        else:
            account["lastEnrichAt"] = int(time.time())
            account.pop("enrichError", None)
        _save()
        return sum(p.get("acs") is not None for p in account.get("points", [])[-limit:])


def _wl(points) -> tuple[int, int]:
    return (sum(p.get("result") == "Victory" for p in points),
            sum(p.get("result") == "Defeat" for p in points))


def _pct(points, minimum: int = _MIN_BUCKET) -> float | None:
    wins, losses = _wl(points)
    return round(100 * wins / (wins + losses), 1) if wins + losses >= minimum else None


_DAYPARTS = (("morning", 5, 12), ("afternoon", 12, 17),
             ("evening", 17, 22), ("night", 22, 29))


def _daypart(hour: int) -> str:
    for name, low, high in _DAYPARTS:
        if low <= hour < high or low <= hour + 24 < high:
            return name
    return "night"


def _insights(points: list[dict], timezone_name: str | None = None) -> list[dict]:
    zone_name, zone = _valid_timezone(timezone_name)
    rated = [p for p in points if p.get("result") in ("Victory", "Defeat") and p.get("ts")]
    if len(rated) < _MIN_TOTAL:
        return []
    out: list[dict] = []

    def add(title, text, tone="neutral", samples=None, confidence="medium"):
        out.append({"title": title, "text": text, "tone": tone,
                    "samples": samples, "confidence": confidence})

    by_part: dict[str, list] = {}
    for point in rated:
        hour = datetime.fromtimestamp(point["ts"], zone).hour
        by_part.setdefault(_daypart(hour), []).append(point)
    parts = [(name, _pct(bucket), len(bucket)) for name, bucket in by_part.items()]
    parts = [item for item in parts if item[1] is not None]
    if len(parts) >= 2:
        parts.sort(key=lambda item: item[1], reverse=True)
        best, worst = parts[0], parts[-1]
        if best[1] - worst[1] >= 10:
            add("Time of day",
                f"{best[0].title()} games are {best[1]:.0f}% wins ({best[2]}), compared with "
                f"{worst[1]:.0f}% across {worst[2]} {worst[0]} games.",
                "pos" if best[1] >= 50 else "neutral", best[2] + worst[2])

    weekends, weekdays = [], []
    for point in rated:
        target = weekends if datetime.fromtimestamp(point["ts"], zone).weekday() >= 5 else weekdays
        target.append(point)
    weekend_pct, weekday_pct = _pct(weekends), _pct(weekdays)
    if weekend_pct is not None and weekday_pct is not None and abs(weekend_pct - weekday_pct) >= 10:
        high, low, high_label, low_label, samples = (
            (weekend_pct, weekday_pct, "weekends", "weekdays", len(weekends) + len(weekdays))
            if weekend_pct > weekday_pct else
            (weekday_pct, weekend_pct, "weekdays", "weekends", len(weekends) + len(weekdays)))
        add("Weekday vs weekend",
            f"{high:.0f}% wins on {high_label}, compared with {low:.0f}% on {low_label}.",
            samples=samples)

    by_map: dict[str, list] = {}
    for point in rated:
        if point.get("map") and point["map"] != "Unknown":
            by_map.setdefault(point["map"], []).append(point)
    maps = [(name, _pct(bucket), len(bucket)) for name, bucket in by_map.items()]
    maps = [item for item in maps if item[1] is not None]
    if maps:
        maps.sort(key=lambda item: item[1], reverse=True)
        best = maps[0]
        add("Strongest map sample",
            f"{best[0]}: {best[1]:.0f}% wins across {best[2]} matches.",
            "pos" if best[1] >= 50 else "neutral", best[2])
        if len(maps) > 1 and maps[-1][1] < 50:
            worst = maps[-1]
            add("Lowest map sample",
                f"{worst[0]}: {worst[1]:.0f}% wins across {worst[2]} matches.",
                "neg", worst[2])

    wins = [p for p in rated if p["result"] == "Victory" and isinstance(p.get("delta"), (int, float))]
    losses = [p for p in rated if p["result"] == "Defeat" and isinstance(p.get("delta"), (int, float))]
    if len(wins) >= _MIN_BUCKET and len(losses) >= _MIN_BUCKET:
        avg_win = sum(p["delta"] for p in wins) / len(wins)
        avg_loss = sum(p["delta"] for p in losses) / len(losses)
        add("RR economy",
            f"Average {avg_win:+.0f} RR on wins and {avg_loss:+.0f} RR on losses.",
            samples=len(wins) + len(losses))

    recent = [p for p in rated if p["ts"] >= time.time() - 7 * 86400
              and isinstance(p.get("delta"), (int, float))]
    if recent:
        net = sum(p["delta"] for p in recent)
        add("Last 7 days", f"{net:+.0f} RR across {len(recent)} competitive matches.",
            "pos" if net > 0 else "neg" if net < 0 else "neutral", len(recent), "low" if len(recent) < 8 else "medium")

    hours: dict[int, int] = {}
    for point in rated:
        hour = datetime.fromtimestamp(point["ts"], zone).hour
        hours[hour] = hours.get(hour, 0) + 1
    if hours:
        hour, count = max(hours.items(), key=lambda item: item[1])
        if count >= _MIN_BUCKET:
            add("Most active hour", f"Your largest start-time sample is {hour:02d}:00 ({count} matches).",
                samples=count)
    return out[:6]


def _summary(points: list[dict]) -> dict:
    rated = [p for p in points if p.get("result") in ("Victory", "Defeat")]
    wins, losses = _wl(rated)
    latest = next((p for p in reversed(points) if p.get("tier") is not None and p.get("rr") is not None), None)
    current = rank_from_tier(latest.get("tier") if latest else 0)
    deltas = [p["delta"] for p in points if isinstance(p.get("delta"), (int, float))]
    win_deltas = [p["delta"] for p in rated if p.get("result") == "Victory" and isinstance(p.get("delta"), (int, float))]
    loss_deltas = [p["delta"] for p in rated if p.get("result") == "Defeat" and isinstance(p.get("delta"), (int, float))]
    next_rank = None
    rr = latest.get("rr") if latest else None
    tier = latest.get("tier") if latest else None
    if isinstance(tier, int) and isinstance(rr, (int, float)) and 3 <= tier < 24:
        next_rank = rank_from_tier(tier + 1)
        next_rank = {**next_rank, "rrNeeded": max(0, 100 - rr), "progress": max(0, min(100, rr))}
    return {
        "matches": wins + losses, "wins": wins, "losses": losses,
        "winRate": round(100 * wins / (wins + losses), 1) if wins + losses else None,
        "net": sum(deltas),
        "avgWin": round(sum(win_deltas) / len(win_deltas), 1) if win_deltas else None,
        "avgLoss": round(sum(loss_deltas) / len(loss_deltas), 1) if loss_deltas else None,
        "current": {**current, "rr": rr, "tier": tier},
        "next": next_rank,
        "exactResults": sum(bool(p.get("resultExact", True)) for p in rated),
    }


def _split_rows(points: list[dict], key: str) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for point in points:
        name = point.get(key)
        if name and name != "Unknown" and point.get("result") in ("Victory", "Defeat"):
            buckets.setdefault(str(name), []).append(point)
    rows = []
    for name, matches in buckets.items():
        wins = sum(p.get("result") == "Victory" for p in matches)
        deltas = [p.get("delta") for p in matches if isinstance(p.get("delta"), (int, float))]
        kds = [p.get("kd") for p in matches if isinstance(p.get("kd"), (int, float))]
        acs = [p.get("acs") for p in matches if isinstance(p.get("acs"), (int, float))]
        rows.append({
            "name": name, "games": len(matches), "wins": wins,
            "losses": len(matches) - wins, "winRate": round(100 * wins / len(matches)),
            "netRr": sum(deltas),
            "avgKd": round(sum(kds) / len(kds), 2) if kds else None,
            "avgAcs": round(sum(acs) / len(acs)) if acs else None,
            "portrait": next((p.get("agentPortrait") for p in matches if p.get("agentPortrait")), None),
            "color": next((p.get("agentColor") for p in matches if p.get("agentColor")), None),
        })
    return sorted(rows, key=lambda row: (-row["games"], -row["winRate"], row["name"]))


def _schedule_rows(points: list[dict], zone: ZoneInfo) -> dict:
    weekdays = {day: [] for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")}
    dayparts = {name: [] for name, _, _ in _DAYPARTS}
    calendar: dict[str, list[dict]] = {}
    for point in points:
        if not point.get("ts") or point.get("result") not in ("Victory", "Defeat"):
            continue
        local = datetime.fromtimestamp(point["ts"], zone)
        weekdays[local.strftime("%a")].append(point)
        dayparts[_daypart(local.hour)].append(point)
        calendar.setdefault(local.strftime("%Y-%m-%d"), []).append(point)

    def summarize(name, matches):
        wins = sum(p.get("result") == "Victory" for p in matches)
        deltas = [p.get("delta") for p in matches if isinstance(p.get("delta"), (int, float))]
        return {"name": name, "games": len(matches), "wins": wins,
                "winRate": round(100 * wins / len(matches)) if matches else None,
                "netRr": sum(deltas)}

    return {
        "weekdays": [summarize(name, matches) for name, matches in weekdays.items()],
        "dayparts": [summarize(name.title(), matches) for name, matches in dayparts.items()],
        "calendar": [summarize(day, matches) for day, matches in sorted(calendar.items())],
    }


def _milestones(points: list[dict]) -> dict:
    promotions, personal = [], {}
    ordered = sorted(points, key=lambda p: p.get("ts") or 0)
    for previous, current in zip(ordered, ordered[1:]):
        if not isinstance(previous.get("tier"), int) or not isinstance(current.get("tier"), int):
            continue
        if current["tier"] != previous["tier"]:
            promotions.append({"matchId": current.get("matchId"), "ts": current.get("ts"),
                               "fromTier": previous["tier"], "toTier": current["tier"],
                               "type": "promotion" if current["tier"] > previous["tier"] else "demotion"})
    rated = [p for p in ordered if p.get("result") in ("Victory", "Defeat")]
    if rated:
        best_delta = max(rated, key=lambda p: p.get("delta") if isinstance(p.get("delta"), (int, float)) else -9999)
        best_acs = max(rated, key=lambda p: p.get("acs") or -1)
        highest = max(rated, key=lambda p: (p.get("tier") or 0) * 100 + (p.get("rr") or 0))
        personal = {"bestRrMatch": best_delta.get("matchId"), "bestRr": best_delta.get("delta"),
                    "bestAcsMatch": best_acs.get("matchId"), "bestAcs": best_acs.get("acs"),
                    "highestTier": highest.get("tier"), "highestRr": highest.get("rr")}
    return {"rankChanges": promotions, "personalBests": personal}


def _act_comparison(points: list[dict]) -> dict | None:
    seasons = []
    for point in points:
        season = point.get("seasonId")
        if season and season not in seasons:
            seasons.append(season)
    if len(seasons) < 2:
        return None
    current, previous = seasons[-1], seasons[-2]
    return {"current": {"id": current, **_summary([p for p in points if p.get("seasonId") == current])},
            "previous": {"id": previous, **_summary([p for p in points if p.get("seasonId") == previous])}}


def payload(puuid: str | None = None, timezone_name: str | None = None) -> dict:
    zone_name, zone = _valid_timezone(timezone_name)
    with _LOCK:
        account = _ensure_account(puuid, timezone_name=timezone_name) if puuid else {}
        if puuid:
            _save()
        points = list(account.get("points", []))
        if not timezone_name:
            zone_name, _ = _valid_timezone(account.get("timezone"))
        account_info = {
            "puuid": puuid,
            "riotId": account.get("riotId"),
            "timezone": zone_name,
        }
    try:
        insights = _insights(points, zone_name)
    except Exception:
        insights = []
    tiers = {int(p["tier"]) for p in points if isinstance(p.get("tier"), int)}
    rank_icons = {str(tier): valapi.cached_rank_icon(tier) for tier in tiers}
    maps = {p.get("map") for p in points if p.get("map") and p.get("map") != "Unknown"}
    map_splashes = {name: valapi.map_splash(name) for name in maps}
    rich = sum(p.get("acs") is not None for p in points[-20:])
    milestones = _milestones(points)
    return {
        "version": 3,
        "account": account_info,
        "points": points,
        "summary": _summary(points),
        "insights": insights,
        "insightsMinimum": _MIN_TOTAL,
        "timezone": zone_name,
        "dataQuality": {
            "exact": sum(bool(p.get("resultExact", True)) for p in points),
            "estimated": sum(not bool(p.get("resultExact", True)) for p in points),
        },
        "rankIcons": rank_icons,
        "mapSplashes": map_splashes,
        "splits": {"maps": _split_rows(points, "map"),
                   "agents": _split_rows(points, "agent"),
                   "schedule": _schedule_rows(points, zone)},
        "actComparison": _act_comparison(points),
        **milestones,
        "enrichment": {"rich": rich, "target": min(20, len(points)),
                       "updatedAt": account.get("lastEnrichAt"),
                       "error": bool(account.get("enrichError"))},
        "backfill": {
            "matches": account.get("backfillMatches", 0),
            "updatedAt": account.get("lastBackfillAt"),
            "error": bool(account.get("backfillError")),
        },
        "discardedOrphanPoints": _STORE.get("discardedOrphanPoints", 0),
    }


if __name__ == "__main__":
    now = int(time.time())
    sample = [{"matchId": f"m{i}", "ts": now - (24 - i) * 3600,
               "map": "Ascent", "result": "Victory" if i % 2 else "Defeat",
               "delta": 20 if i % 2 else -18, "tier": 16, "rr": 50,
               "resultExact": True} for i in range(24)]
    assert _insights(sample, "UTC")
    assert _insights(sample[:2], "UTC") == []
    assert {_daypart(h) for h in range(24)} == {"morning", "afternoon", "evening", "night"}
    print("history self-check OK")
