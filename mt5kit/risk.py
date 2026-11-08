"""ความเสี่ยงและการคำนวณล็อต (ข้อ 9 ของ plan.md)

วิธีคิดเงินต่อ lot ใช้สูตรชั่วคราว: ระยะราคา / tick_size × tick_value (tick_value อยู่ในสกุลบัญชี
ตามราคาปัจจุบันของ MT5) ต้องเทียบกับ Deal จริงบน Demo ก่อนใช้เงินจริง (ระยะที่ 3)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .adapters import Account, Order, Position, SymbolInfo


class RiskDataError(ValueError):
    pass


def money_per_lot(sym: SymbolInfo, price_dist: float, loss: bool = True) -> float:
    tv = sym.tick_value_loss if loss else sym.tick_value
    if not sym.tick_size or not tv:
        raise RiskDataError(f"{sym.name}: tick_size/tick_value เป็นศูนย์หรือไม่มี")
    return abs(price_dist) / sym.tick_size * tv


def round_price(sym: SymbolInfo, p: float, mode: str = "nearest") -> float:
    ts = sym.tick_size or sym.point
    q = p / ts
    q = math.floor(q + 1e-9) if mode == "down" else math.ceil(q - 1e-9) if mode == "up" else round(q)
    return round(q * ts, sym.digits)


def floor_volume(sym: SymbolInfo, v: float) -> float:
    if not sym.vol_step:
        raise RiskDataError(f"{sym.name}: ไม่มี volume_step")
    steps = math.floor(v / sym.vol_step + 1e-9)
    dec = max(0, -int(math.floor(math.log10(sym.vol_step)))) if sym.vol_step < 1 else 0
    return round(steps * sym.vol_step, dec + 2)


def swap_cost_per_lot(sym: SymbolInfo, side: str, nights: int) -> tuple[float, str | None]:
    """ประมาณ Swap ติดลบต่อ lot (คิดเฉพาะกรณีติดลบ) คืน (เงิน, ข้อจำกัด)"""
    sw = sym.swap_long if side == "long" else sym.swap_short
    if sw >= 0:
        return 0.0, None
    mode = sym.swap_mode
    if "point" in mode or mode in ("1", "unknown"):
        per_night = abs(sw) * sym.point / sym.tick_size * sym.tick_value_loss if sym.tick_size else 0.0
        note = None if "point" in mode or mode == "1" else "swap_mode ไม่ทราบ — ประมาณเป็น point"
        return per_night * nights, note
    if "currency" in mode or "deposit" in mode or mode in ("2", "3", "4"):
        return abs(sw) * nights, None
    return 0.0, f"swap_mode '{mode}' ยังไม่รองรับ — ไม่ได้รวม Swap"


@dataclass
class Exposure:
    total: float = 0.0
    unknown: list = field(default_factory=list)
    by_ccy: dict = field(default_factory=dict)       # {"USD": {"long": x, "short": y}}
    items: list = field(default_factory=list)
    ideas: int = 0
    symbols: set = field(default_factory=set)

    def add_ccy(self, base: str, quote: str, side: str, risk: float):
        def add(c, s):
            self.by_ccy.setdefault(c, {"long": 0.0, "short": 0.0})[s] += risk
        add(base, side)
        add(quote, "short" if side == "long" else "long")


def portfolio_exposure(positions: list[Position], orders: list[Order], symbols: dict[str, SymbolInfo]) -> Exposure:
    ex = Exposure()
    for p in positions:
        sym = symbols.get(p.symbol)
        item = {"kind": "position", "ticket": p.ticket, "symbol": p.symbol, "side": p.side, "volume": p.volume, "sl": p.sl}
        if not sym or not p.sl:
            ex.unknown.append({**item, "reason": "ไม่มี SL" if not p.sl else "ไม่มีข้อมูล Symbol"})
            ex.symbols.add(p.symbol); ex.ideas += 1
            continue
        px = p.price_current or (sym.bid if p.side == "long" else sym.ask)
        dist = (px - p.sl) if p.side == "long" else (p.sl - px)
        try:
            r = max(0.0, money_per_lot(sym, dist) * p.volume) if dist > 0 else 0.0
        except RiskDataError as e:
            ex.unknown.append({**item, "reason": str(e)}); continue
        ex.total += r
        ex.add_ccy(sym.base, sym.quote, p.side, r)
        ex.items.append({**item, "remaining_risk": r})
        ex.symbols.add(p.symbol); ex.ideas += 1
    for o in orders:
        sym = symbols.get(o.symbol)
        item = {"kind": "pending", "ticket": o.ticket, "symbol": o.symbol, "side": o.side, "volume": o.volume, "sl": o.sl}
        if not sym or not o.sl:
            ex.unknown.append({**item, "reason": "ไม่มี SL" if not o.sl else "ไม่มีข้อมูล Symbol"})
            ex.symbols.add(o.symbol); ex.ideas += 1
            continue
        try:
            r = money_per_lot(sym, o.price - o.sl) * o.volume
        except RiskDataError as e:
            ex.unknown.append({**item, "reason": str(e)}); continue
        ex.total += r
        ex.add_ccy(sym.base, sym.quote, o.side, r)
        ex.items.append({**item, "remaining_risk": r})
        ex.symbols.add(o.symbol); ex.ideas += 1
    return ex


@dataclass
class SizeResult:
    ok: bool
    volume: float = 0.0
    risk_money: float = 0.0
    loss_per_lot: float = 0.0
    cost_per_lot: float = 0.0
    budget: float = 0.0
    reason: str = ""
    notes: list = field(default_factory=list)


def size_position(acct: Account, sym: SymbolInfo, side: str, entry: float, sl: float, ex: Exposure,
                  profile: dict, dd_multiplier: float, strategy: dict) -> SizeResult:
    rp = profile["risk"]
    base = min(acct.balance, acct.equity)
    idea_budget = base * min(rp["risk_per_idea"], rp["max_risk_per_idea"]) * dd_multiplier
    port_left = base * rp["portfolio_cap"] - ex.total
    opp = "short" if side == "long" else "long"
    b_used = ex.by_ccy.get(sym.base, {}).get(side, 0.0)
    q_used = ex.by_ccy.get(sym.quote, {}).get(opp, 0.0)
    ccy_left = base * rp["currency_cap"] - max(b_used, q_used)
    budget = min(idea_budget, port_left, ccy_left)
    res = SizeResult(ok=False, budget=budget)
    if budget <= 0:
        res.reason = "เพดานความเสี่ยงพอร์ตหรือสกุลเงินเต็ม"
        return res
    try:
        lpl = money_per_lot(sym, entry - sl)
    except RiskDataError as e:
        res.reason = f"BLOCKED_DATA: {e}"
        return res
    comm = profile["costs"]["commission_per_lot_roundtrip"]
    commission = float(comm.get(sym.name, comm.get("default", 0.0)))
    slip = profile["costs"]["slippage_ticks"] * sym.tick_value_loss
    swap, note = swap_cost_per_lot(sym, side, strategy["cost"]["swap_nights_assumed"])
    if note:
        res.notes.append(note)
    cpl = commission + slip + swap
    raw = budget / (lpl + cpl)
    vol = floor_volume(sym, raw)
    if sym.vol_max:
        vol = min(vol, sym.vol_max)
    if vol < sym.vol_min or vol <= 0:
        res.reason = f"ล็อตที่คำนวณได้ ({raw:.4f}) ต่ำกว่าขั้นต่ำ {sym.vol_min}"
        res.loss_per_lot, res.cost_per_lot = lpl, cpl
        return res
    res.ok, res.volume, res.loss_per_lot, res.cost_per_lot = True, vol, lpl, cpl
    res.risk_money = vol * (lpl + cpl)
    return res


def approx_margin(sym: SymbolInfo, acct: Account, volume: float, price: float) -> tuple[float | None, str]:
    """ประมาณมาร์จิน (ยังไม่มี OrderCalcMargin) คืน (เงิน, วิธีคิด)"""
    if sym.margin_initial:
        return sym.margin_initial * volume, "margin_initial"
    if not acct.leverage or not sym.contract_size:
        return None, "ไม่มี leverage/contract_size"
    notional_quote = volume * sym.contract_size * price
    # แปลงจากสกุล quote เป็นสกุลบัญชี โดยใช้ tick_value (เงินบัญชีต่อ tick ต่อ lot) / (tick_size × contract_size)
    conv = sym.tick_value / (sym.tick_size * sym.contract_size) if sym.tick_size and sym.contract_size else None
    if conv is None:
        return None, "แปลงสกุลไม่ได้"
    return notional_quote * conv / acct.leverage, "ประมาณจาก leverage"


def dd_multiplier(drawdown: float | None, profile: dict) -> tuple[float, str | None]:
    rp = profile["risk"]
    if drawdown is None:
        return 1.0, None
    if drawdown >= rp["dd_stop"]:
        return 0.0, f"Drawdown {drawdown:.1%} ≥ {rp['dd_stop']:.0%} — หยุดเปิดใหม่"
    if drawdown >= rp["dd_half_risk"]:
        return 0.5, f"Drawdown {drawdown:.1%} ≥ {rp['dd_half_risk']:.0%} — ลดความเสี่ยงครึ่งหนึ่ง"
    return 1.0, None
