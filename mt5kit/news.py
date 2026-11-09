"""ข่าว: แบ่ง Tier และหาช่วงงดข่าว (ข้อ 7.1)"""
from __future__ import annotations

import json
from pathlib import Path

from .adapters import NewsEvent, instrument_currencies

CONFIG = Path(__file__).parent / "config" / "news-tier1.json"


def load_tier1(path: Path = CONFIG) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def classify(events: list[NewsEvent], tier1: dict | None = None) -> list[NewsEvent]:
    tier1 = tier1 or load_tier1()
    common = [w.lower() for w in tier1.get("common", [])]
    excl = [w.lower() for w in tier1.get("exclude", [])]
    for e in events:
        name = e.name.lower()
        words = common + [w.lower() for w in tier1.get("by_currency", {}).get(e.currency, [])]
        is_t1 = any(w in name for w in words) and not any(x in name for x in excl)
        if is_t1 and e.importance in ("high", "unknown", "medium"):
            e.tier = 1
        elif e.importance == "high":
            e.tier = 2
        else:
            e.tier = 0
    return events


def pair_currencies(symbol: str, base: str | None = None, quote: str | None = None) -> tuple[str, str]:
    return instrument_currencies(symbol, base, quote)


def events_for(events: list[NewsEvent], ccys: tuple[str, str], tier: int | None = None) -> list[NewsEvent]:
    return [e for e in events if e.currency in ccys and (tier is None or e.tier == tier)]


def tier1_blackouts(events: list[NewsEvent], ccys, before_min: int, after_min: int) -> list[tuple[float, float, NewsEvent]]:
    return [(e.t - before_min * 60, e.t + after_min * 60, e) for e in events_for(events, ccys, 1)]


def bar_contains_event(bar_open: float, bar_seconds: int, events: list[NewsEvent], ccys, tier: int) -> NewsEvent | None:
    for e in events_for(events, ccys, tier):
        if bar_open <= e.t < bar_open + bar_seconds:
            return e
    return None


def blocked_now(now: float, events, ccys, cfg: dict) -> NewsEvent | None:
    for a, b, e in tier1_blackouts(events, ccys, cfg["tier1_before_min"], cfg["tier1_after_min"]):
        if a <= now <= b:
            return e
    return None


def next_tier1_start(now: float, events, ccys, cfg: dict) -> tuple[float, NewsEvent] | None:
    starts = [(a, e) for a, b, e in tier1_blackouts(events, ccys, cfg["tier1_before_min"], cfg["tier1_after_min"]) if b >= now]
    return min(starts, key=lambda x: x[0]) if starts else None


def first_h1_close_after(e_t: float) -> float:
    """เวลาปิดของแท่ง H1 แรกที่เริ่มหลังข่าว"""
    return (int(e_t // 3600) + 1) * 3600 + 3600


def tier2_conflict(entry_from: float, entry_to: float, events, ccys, window_min: int) -> NewsEvent | None:
    """Pending ที่มีอายุคร่อมข่าว Tier 2 ±window"""
    for e in events_for(events, ccys, 2):
        if entry_from <= e.t + window_min * 60 and e.t - window_min * 60 <= entry_to:
            return e
    return None
