"""สร้างข้อมูลจำลองในรูปแบบผลดิบของ MCP เพื่อทดสอบโค้ด (ไม่ใช่ข้อมูลจริง ใช้วัดกำไรไม่ได้)"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")

PAIRS = {
    "EURUSD": (1.08, 5, 1.0), "GBPUSD": (1.27, 5, 1.0), "AUDUSD": (0.66, 5, 1.0), "NZDUSD": (0.60, 5, 1.0),
    "USDJPY": (150.0, 3, None), "USDCAD": (1.37, 5, None), "EURJPY": (162.0, 3, None), "GBPJPY": (190.0, 3, None),
    "AUDJPY": (99.0, 3, None), "EURGBP": (0.85, 5, None),
}


def server_of(utc_dt: datetime) -> datetime:
    return utc_dt.astimezone(NY).replace(tzinfo=None) + timedelta(hours=7)


def utc_of(server_dt: datetime) -> datetime:
    return (server_dt - timedelta(hours=7)).replace(tzinfo=NY).astimezone(UTC)


def gen_h1(symbol: str, start_server: datetime, n: int, seed: int) -> list[dict]:
    p0, digits, _ = PAIRS[symbol]
    rnd = random.Random(seed)
    px = p0
    out = []
    t = start_server
    drift, regime_left = 0.0, 0
    sig = 0.0011 * p0 if digits == 5 else 0.0011 * p0
    while len(out) < n:
        if t.weekday() >= 5:  # เซิร์ฟเวอร์ Sat/Sun ปิด
            t += timedelta(hours=1)
            continue
        if regime_left <= 0:
            drift = rnd.choice([-1, 0, 1]) * rnd.uniform(0.05, 0.22) * sig
            regime_left = rnd.randint(60, 300)
        regime_left -= 1
        u = utc_of(t)
        hour_lon = u.astimezone(ZoneInfo("Europe/London")).hour
        mult = 1.6 if 7 <= hour_lon <= 16 else 0.6
        o = px
        pts = [o]
        for _ in range(4):
            pts.append(pts[-1] + drift / 4 + rnd.gauss(0, sig * mult / 2))
        c = pts[-1]
        h = max(pts) + abs(rnd.gauss(0, sig * 0.25))
        l = min(pts) - abs(rnd.gauss(0, sig * 0.25))
        out.append({"time": t.strftime("%Y.%m.%d %H:%M"), "open": round(o, digits), "high": round(h, digits), "low": round(l, digits),
                    "close": round(c, digits), "tick_volume": rnd.randint(500, 5000), "spread": rnd.randint(5, 14) if digits == 5 else rnd.randint(8, 20)})
        px = c
        t += timedelta(hours=1)
    return out


def aggregate(h1: list[dict], tf: str) -> list[dict]:
    groups: dict = {}
    order = []
    for b in h1:
        t = datetime.strptime(b["time"], "%Y.%m.%d %H:%M")
        if tf == "H4":
            k = t.replace(hour=t.hour - t.hour % 4)
        elif tf == "D1":
            k = t.replace(hour=0)
        else:
            k = (t - timedelta(days=t.weekday())).replace(hour=0)
        if k not in groups:
            groups[k] = dict(b, time=k.strftime("%Y.%m.%d %H:%M"))
            order.append(k)
        else:
            g = groups[k]
            g["high"] = max(g["high"], b["high"]); g["low"] = min(g["low"], b["low"]); g["close"] = b["close"]
            g["tick_volume"] += b["tick_volume"]
    return [groups[k] for k in order]


def symbol_rec(name: str, bid: float) -> dict:
    p0, digits, tv = PAIRS[name]
    point = 10 ** -digits
    quote = name[3:]
    if tv is None:
        if quote == "JPY":
            tv = 100000 * point / (bid if name == "USDJPY" else 150.0)
        elif quote == "CAD":
            tv = 100000 * point / bid
        elif quote == "GBP":
            tv = 100000 * point * 1.27
    spread = 8 * point if digits == 5 else 12 * point
    return {"name": name, "digits": digits, "point": point, "trade_tick_size": point, "trade_tick_value": round(tv, 6),
            "trade_tick_value_loss": round(tv, 6), "trade_contract_size": 100000, "volume_min": 0.01, "volume_max": 50,
            "volume_step": 0.01, "volume_limit": 0, "trade_stops_level": 0, "trade_freeze_level": 0,
            "bid": round(bid, digits), "ask": round(bid + spread, digits), "currency_base": name[:3], "currency_profit": quote,
            "swap_long": -6.5, "swap_short": 1.2, "swap_mode": "SYMBOL_SWAP_MODE_POINTS", "trade_mode": "SYMBOL_TRADE_MODE_FULL",
            "expiration_mode": "GTC|DAY|SPECIFIED|SPECIFIED_DAY", "filling_mode": "RETURN"}


def calendar(start_utc: datetime, end_utc: datetime) -> list[dict]:
    ev = []
    d = start_utc.date()
    while d <= end_utc.date():
        wd = d.weekday()
        if wd == 4:
            ev.append(("USD", "Nonfarm Payrolls", "high", 8, 30, NY))
        if wd == 2:
            ev.append(("USD", "CPI m/m", "high", 8, 30, NY))
            ev.append(("GBP", "Retail Sales m/m", "high", 7, 0, ZoneInfo("Europe/London")))
        if wd == 3:
            ev.append(("EUR", "German Flash Manufacturing PMI", "high", 9, 30, ZoneInfo("Europe/Berlin")))
            ev.append(("AUD", "Employment Change", "high", 11, 30, ZoneInfo("Australia/Sydney")))
        if wd == 1:
            ev.append(("JPY", "BOJ Policy Rate", "high", 12, 0, ZoneInfo("Asia/Tokyo")))
        out_today = []
        for ccy, name, imp, hh, mm, tz in ev:
            t = datetime(d.year, d.month, d.day, hh, mm, tzinfo=tz).astimezone(UTC)
            out_today.append({"time": server_of(t).strftime("%Y.%m.%d %H:%M"), "currency": ccy, "importance": imp, "event": name,
                              "forecast": "", "previous": ""})
        ev = []
        for x in out_today:
            yield x
        d += timedelta(days=1)


def build(out: Path, n_h1: int = 7600, seed: int = 7, cut_utc: datetime | None = None):
    """สร้าง fixture ครบชุด; ถ้าให้ cut_utc จะตัดแท่งที่ยังไม่ปิด ณ เวลานั้นออก"""
    out.mkdir(parents=True, exist_ok=True)
    end_server = server_of(datetime(2026, 11, 6, 20, 0, tzinfo=UTC))
    start = end_server - timedelta(hours=int(n_h1 * 1.42))
    full = {}
    for i, s in enumerate(PAIRS):
        full[s] = gen_h1(s, start, n_h1, seed * 100 + i)
    return full


def write_fixture(out: Path, full: dict, now_utc: datetime, account_extra: dict | None = None, positions: dict | None = None):
    out.mkdir(parents=True, exist_ok=True)
    now_srv = server_of(now_utc)
    syms = []
    for s, h1 in full.items():
        h1c = [b for b in h1 if datetime.strptime(b["time"], "%Y.%m.%d %H:%M") + timedelta(hours=1) <= now_srv]
        bars = {"H1": h1c[-1000:], "H4": aggregate(h1c, "H4")[-500:], "D1": aggregate(h1c, "D1")[-300:], "W1": aggregate(h1c, "W1")[-60:]}
        for tf, b in bars.items():
            (out / f"bars_{s}_{tf}.json").write_text(json.dumps({"symbol": s, "timeframe": tf, "rates": b}))
        syms.append(symbol_rec(s, h1c[-1]["close"]))
    (out / "symbols.json").write_text(json.dumps({"symbols": syms}))
    acct = {"login": 51234567, "server": "Demo-Broker-Server", "currency": "USD", "balance": 10000.0, "equity": 10000.0,
            "margin": 0.0, "margin_free": 10000.0, "margin_level": 0, "leverage": 100,
            "margin_mode": "ACCOUNT_MARGIN_MODE_RETAIL_HEDGING", "trade_mode": "ACCOUNT_TRADE_MODE_DEMO"}
    acct.update(account_extra or {})
    (out / "account.json").write_text(json.dumps(acct))
    (out / "positions.json").write_text(json.dumps(positions or {"positions": [], "orders": []}))
    (out / "calendar.json").write_text(json.dumps(list(calendar(now_utc - timedelta(days=3), now_utc + timedelta(days=4)))))
    (out / "time.json").write_text(json.dumps({"server_time": now_srv.strftime("%Y.%m.%d %H:%M:%S"), "gmt_time": now_utc.strftime("%Y.%m.%d %H:%M:%S")}))
