"""แปลงข้อมูลดิบจาก MCP ให้เป็นรูปแบบมาตรฐาน

รูปแบบผลลัพธ์จริงของเครื่องมือ MCP ยังไม่ได้ยืนยัน (ดู `python3 -m mt5kit probe`)
จึงใช้วิธีหาชื่อฟิลด์หลายแบบ ถ้าอ่านไม่ได้จะ error ชัดเจน ห้ามเดาค่า
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

from .timeutil import ServerClock, parse_server_time


class AdapterError(ValueError):
    pass


def _lower_keys(d: dict) -> dict:
    return {str(k).lower(): v for k, v in d.items()}


def _pick(d: dict, *names, default=None):
    for n in names:
        if n in d and d[n] is not None and d[n] != "":
            return d[n]
    return default


def _num(v, default=None):
    if v is None or v == "":
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def find_records(payload: Any, must_have: tuple[str, ...]) -> list[dict]:
    """หา list ของ dict ที่มีคีย์ครบตาม must_have (ชื่อคีย์ตัวเล็ก) ในข้อมูลซ้อนกันกี่ชั้นก็ได้"""
    if isinstance(payload, list):
        if payload and all(isinstance(x, dict) for x in payload):
            lk = _lower_keys(payload[0])
            if all(any(alt in lk for alt in m.split("|")) for m in must_have):
                return payload
        for x in payload:
            r = find_records(x, must_have)
            if r:
                return r
    elif isinstance(payload, dict):
        # dict ของ array คอลัมน์ เช่น {"time":[...], "open":[...]}
        lk = _lower_keys(payload)
        if all(any(alt in lk and isinstance(lk[alt], list) for alt in m.split("|")) for m in must_have):
            cols = {k: v for k, v in lk.items() if isinstance(v, list)}
            n = min(len(v) for v in cols.values())
            return [{k: cols[k][i] for k in cols} for i in range(n)]
        for v in payload.values():
            r = find_records(v, must_have)
            if r:
                return r
    return []


# ---------------- bars ----------------
@dataclass
class Bar:
    t: float          # เวลาเปิดแท่ง UTC (epoch วินาที)
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0
    spread: float = 0.0  # หน่วย point


TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800}


def parse_bars(payload: Any, clock: ServerClock) -> list[Bar]:
    recs = find_records(payload, ("time|t|datetime|date|timestamp", "open|o", "high|h", "low|l", "close|c"))
    if not recs:
        raise AdapterError("อ่านแท่งเทียนไม่ได้: ไม่พบฟิลด์ time/open/high/low/close")
    out = []
    for r in recs:
        d = _lower_keys(r)
        tv = _pick(d, "time", "t", "datetime", "date", "timestamp")
        dt_utc = clock.to_utc(parse_server_time(tv))
        out.append(Bar(
            t=dt_utc.timestamp(),
            o=float(_pick(d, "open", "o")), h=float(_pick(d, "high", "h")),
            l=float(_pick(d, "low", "l")), c=float(_pick(d, "close", "c")),
            v=_num(_pick(d, "tick_volume", "volume", "v", "real_volume"), 0.0),
            spread=_num(_pick(d, "spread"), 0.0),
        ))
    out.sort(key=lambda b: b.t)
    # ตัดแท่งซ้ำ
    dedup = []
    for b in out:
        if dedup and dedup[-1].t == b.t:
            dedup[-1] = b
        else:
            dedup.append(b)
    return dedup


def closed_bars(bars: list[Bar], tf: str, now_utc: float) -> list[Bar]:
    """เก็บเฉพาะแท่งที่ปิดแล้ว"""
    sec = TF_SECONDS[tf]
    if tf == "W1":
        return [b for b in bars if b.t + sec - 2 * 86400 <= now_utc]  # แท่งสัปดาห์ปิดวันศุกร์
    if tf == "D1":
        return [b for b in bars if b.t + sec <= now_utc + 60]
    return [b for b in bars if b.t + sec <= now_utc + 1]


# ---------------- account ----------------
@dataclass
class Account:
    login: str
    server: str
    currency: str
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float | None
    leverage: float | None
    mode: str            # hedging / netting / unknown
    trade_mode: str      # demo / real / contest / unknown
    raw_keys: list = field(default_factory=list)


def parse_account(payload: Any) -> Account:
    d = payload
    if isinstance(d, dict) and len(d) == 1 and isinstance(next(iter(d.values())), dict):
        d = next(iter(d.values()))
    if not isinstance(d, dict):
        raise AdapterError("อ่านข้อมูลบัญชีไม่ได้")
    k = _lower_keys(d)
    bal, eq = _num(_pick(k, "balance")), _num(_pick(k, "equity"))
    if bal is None or eq is None:
        raise AdapterError("ข้อมูลบัญชีไม่มี balance/equity")
    mm = str(_pick(k, "margin_mode", "marginmode", "account_margin_mode", default="")).lower()
    mode = "hedging" if "hedg" in mm else "netting" if ("net" in mm or "exchange" in mm) else "unknown"
    tm = str(_pick(k, "trade_mode", "trademode", "account_trade_mode", "type", default="")).lower()
    trade_mode = "demo" if "demo" in tm else "real" if "real" in tm else "contest" if "contest" in tm else "unknown"
    return Account(
        login=str(_pick(k, "login", "account", "account_id", "id", default="?")),
        server=str(_pick(k, "server", default="?")),
        currency=str(_pick(k, "currency", "account_currency", default="?")),
        balance=bal, equity=eq,
        margin=_num(_pick(k, "margin", "margin_used"), 0.0),
        free_margin=_num(_pick(k, "margin_free", "free_margin", "freemargin"), 0.0),
        margin_level=_num(_pick(k, "margin_level", "marginlevel")),
        leverage=_num(_pick(k, "leverage")),
        mode=mode, trade_mode=trade_mode, raw_keys=sorted(k.keys()),
    )


# ---------------- positions / orders ----------------
@dataclass
class Position:
    ticket: str
    symbol: str
    side: str            # long / short
    volume: float
    price_open: float
    sl: float
    tp: float
    price_current: float | None
    profit: float | None
    swap: float | None
    time_utc: float | None
    magic: int | None
    comment: str
    identifier: str | None = None


@dataclass
class Order:
    ticket: str
    symbol: str
    side: str
    order_type: str      # buy_limit / sell_limit / buy_stop / sell_stop / ...
    volume: float
    price: float
    sl: float
    tp: float
    expiry_utc: float | None
    magic: int | None
    comment: str


def _side_from_type(t) -> str:
    s = str(t).lower()
    if s in ("0", "2", "4", "6") or "buy" in s:
        return "long"
    if s in ("1", "3", "5", "7") or "sell" in s:
        return "short"
    return "unknown"


_ORDER_TYPE_NUM = {"2": "buy_limit", "3": "sell_limit", "4": "buy_stop", "5": "sell_stop", "6": "buy_stop_limit", "7": "sell_stop_limit"}


def _order_type(t) -> str:
    s = str(t).lower().replace("order_type_", "").replace(" ", "_")
    return _ORDER_TYPE_NUM.get(s, s)


def parse_positions_orders(payload: Any, clock: ServerClock) -> tuple[list[Position], list[Order]]:
    positions, orders = [], []
    pos_recs, ord_recs = [], []
    if isinstance(payload, dict):
        lk = _lower_keys(payload)
        pos_recs = lk.get("positions") or []
        ord_recs = lk.get("orders") or lk.get("pending_orders") or lk.get("pending") or []
        if not pos_recs and not ord_recs:
            recs = find_records(payload, ("ticket|order|position|id", "symbol"))
            pos_recs = recs
    elif isinstance(payload, list):
        pos_recs = payload
    for r in pos_recs:
        k = _lower_keys(r)
        typ = _pick(k, "type", "side", "direction", default="")
        if any(x in str(typ).lower() for x in ("limit", "stop")) or str(typ) in _ORDER_TYPE_NUM:
            ord_recs.append(r)
            continue
        positions.append(Position(
            ticket=str(_pick(k, "ticket", "position", "id", default="?")),
            symbol=str(_pick(k, "symbol")), side=_side_from_type(typ),
            volume=_num(_pick(k, "volume", "lots"), 0.0),
            price_open=_num(_pick(k, "price_open", "open_price", "price"), 0.0),
            sl=_num(_pick(k, "sl", "stop_loss"), 0.0) or 0.0, tp=_num(_pick(k, "tp", "take_profit"), 0.0) or 0.0,
            price_current=_num(_pick(k, "price_current", "current_price")),
            profit=_num(_pick(k, "profit")), swap=_num(_pick(k, "swap")),
            time_utc=_time_or_none(_pick(k, "time", "time_open", "open_time"), clock),
            magic=_int_or_none(_pick(k, "magic")), comment=str(_pick(k, "comment", default="")),
            identifier=str(_pick(k, "identifier", "position_id", default="")) or None,
        ))
    for r in ord_recs:
        k = _lower_keys(r)
        typ = _pick(k, "type", "order_type", default="")
        orders.append(Order(
            ticket=str(_pick(k, "ticket", "order", "id", default="?")),
            symbol=str(_pick(k, "symbol")), side=_side_from_type(typ), order_type=_order_type(typ),
            volume=_num(_pick(k, "volume_current", "volume", "volume_initial", "lots"), 0.0),
            price=_num(_pick(k, "price_open", "price", "open_price"), 0.0),
            sl=_num(_pick(k, "sl", "stop_loss"), 0.0) or 0.0, tp=_num(_pick(k, "tp", "take_profit"), 0.0) or 0.0,
            expiry_utc=_time_or_none(_pick(k, "time_expiration", "expiration", "expiry"), clock),
            magic=_int_or_none(_pick(k, "magic")), comment=str(_pick(k, "comment", default="")),
        ))
    return positions, orders


def _int_or_none(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _time_or_none(v, clock: ServerClock):
    if v in (None, "", 0, "0", "1970.01.01 00:00", "1970.01.01 00:00:00"):
        return None
    try:
        return clock.to_utc(parse_server_time(v)).timestamp()
    except ValueError:
        return None


# ---------------- symbol ----------------
@dataclass
class SymbolInfo:
    name: str
    base: str
    quote: str
    profit_ccy: str
    digits: int
    point: float
    tick_size: float
    tick_value: float          # ต่อ 1 lot ต่อ 1 tick ในสกุลบัญชี
    tick_value_loss: float
    contract_size: float
    vol_min: float
    vol_max: float
    vol_step: float
    vol_limit: float
    stops_level: int
    freeze_level: int
    bid: float
    ask: float
    quote_time_utc: float | None
    swap_long: float
    swap_short: float
    swap_mode: str
    trade_allowed: bool
    expiration_modes: str
    filling: str
    margin_initial: float | None
    missing: list = field(default_factory=list)

    @property
    def spread(self) -> float:
        return self.ask - self.bid


REQUIRED_SYMBOL = ("point", "tick_size", "tick_value", "contract_size", "vol_min", "vol_step", "bid", "ask")


def parse_symbol(rec: dict, clock: ServerClock) -> SymbolInfo:
    k = _lower_keys(rec)
    name = str(_pick(k, "name", "symbol"))
    digits = int(_num(_pick(k, "digits"), 5))
    point = _num(_pick(k, "point"), None) or (10 ** -digits if "digits" in k else None)
    tick_size = _num(_pick(k, "trade_tick_size", "tick_size", "ticksize"), None) or point
    tv = _num(_pick(k, "trade_tick_value", "tick_value", "tickvalue"))
    tvl = _num(_pick(k, "trade_tick_value_loss", "tick_value_loss"), None) or tv
    base = str(_pick(k, "currency_base", "base_currency", "base", default=name[:3]))
    quote = str(_pick(k, "currency_profit", "currency_quote", "quote_currency", "profit_currency", default=name[3:6]))
    s = SymbolInfo(
        name=name, base=base.upper(), quote=quote.upper(), profit_ccy=quote.upper(),
        digits=digits, point=point or 0.0, tick_size=tick_size or 0.0,
        tick_value=tv or 0.0, tick_value_loss=tvl or 0.0,
        contract_size=_num(_pick(k, "trade_contract_size", "contract_size", "contractsize"), 0.0),
        vol_min=_num(_pick(k, "volume_min", "vol_min", "min_volume", "lot_min"), 0.0),
        vol_max=_num(_pick(k, "volume_max", "vol_max", "max_volume", "lot_max"), 0.0),
        vol_step=_num(_pick(k, "volume_step", "vol_step", "lot_step"), 0.0),
        vol_limit=_num(_pick(k, "volume_limit", "vol_limit"), 0.0),
        stops_level=int(_num(_pick(k, "trade_stops_level", "stops_level", "stoplevel"), 0)),
        freeze_level=int(_num(_pick(k, "trade_freeze_level", "freeze_level"), 0)),
        bid=_num(_pick(k, "bid"), 0.0), ask=_num(_pick(k, "ask"), 0.0),
        quote_time_utc=_time_or_none(_pick(k, "time", "quote_time", "last_quote_time"), clock),
        swap_long=_num(_pick(k, "swap_long"), 0.0), swap_short=_num(_pick(k, "swap_short"), 0.0),
        swap_mode=str(_pick(k, "swap_mode", default="unknown")).lower(),
        trade_allowed=str(_pick(k, "trade_mode", "trade_allowed", default="full")).lower() not in ("0", "disabled", "false", "symbol_trade_mode_disabled", "close_only"),
        expiration_modes=str(_pick(k, "expiration_mode", "expiration_modes", default="unknown")),
        filling=str(_pick(k, "filling_mode", "filling", default="unknown")),
        margin_initial=_num(_pick(k, "margin_initial")),
    )
    missing = [f for f in REQUIRED_SYMBOL if not getattr(s, f)]
    s.missing = missing
    return s


def parse_symbols(payload: Any, clock: ServerClock) -> dict[str, SymbolInfo]:
    recs = find_records(payload, ("name|symbol",))
    if not recs and isinstance(payload, dict) and _lower_keys(payload).get("name"):
        recs = [payload]
    return {str(_lower_keys(r).get("name") or _lower_keys(r).get("symbol")): parse_symbol(r, clock) for r in recs}


# ---------------- calendar ----------------
@dataclass
class NewsEvent:
    t: float
    currency: str
    importance: str      # high / medium / low / unknown
    name: str
    actual: str = ""
    forecast: str = ""
    previous: str = ""
    tier: int = 0


def parse_calendar(payload: Any, clock: ServerClock) -> list[NewsEvent]:
    recs = find_records(payload, ("time|datetime|date|event_time", "currency|country|ccy"))
    out = []
    for r in recs:
        k = _lower_keys(r)
        try:
            t = clock.to_utc(parse_server_time(_pick(k, "time", "datetime", "event_time", "date"))).timestamp()
        except (ValueError, TypeError):
            continue
        imp = str(_pick(k, "importance", "impact", "priority", default="unknown")).lower()
        imp = "high" if ("high" in imp or imp == "3") else "medium" if ("moder" in imp or "medium" in imp or imp == "2") else "low" if ("low" in imp or imp == "1") else imp
        out.append(NewsEvent(
            t=t, currency=str(_pick(k, "currency", "ccy", "country_currency", "country", default="")).upper()[:3],
            importance=imp, name=str(_pick(k, "event", "name", "event_name", "title", default="")),
            actual=str(_pick(k, "actual", "actual_value", default="")), forecast=str(_pick(k, "forecast", "forecast_value", default="")),
            previous=str(_pick(k, "previous", "prev_value", "previous_value", default="")),
        ))
    out.sort(key=lambda e: e.t)
    return out


def to_dict(obj) -> dict:
    return asdict(obj)
