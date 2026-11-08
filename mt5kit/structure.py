"""นิยามโครงสร้างราคา (ข้อ 5.1 ของ plan.md) — ทุกฟังก์ชันใช้เฉพาะแท่งที่ปิดแล้ว และไม่มองอนาคต"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .adapters import Bar


@dataclass
class Swing:
    idx: int
    price: float
    kind: str            # "high" / "low"
    confirm_idx: int     # index ของแท่งที่ทำให้ยืนยัน (idx + right)
    t: float


@dataclass
class FVG:
    idx3: int            # แท่งที่ 3
    top: float
    bottom: float
    direction: str       # "bull" / "bear"
    t1: float            # เวลาแท่งที่ 1
    t3: float

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2


def atr_wilder(bars: list[Bar], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    trs = []
    prev_atr = None
    for i, b in enumerate(bars):
        tr = b.h - b.l if i == 0 else max(b.h - b.l, abs(b.h - bars[i - 1].c), abs(b.l - bars[i - 1].c))
        trs.append(tr)
        if i + 1 == n:
            prev_atr = sum(trs) / n
            out[i] = prev_atr
        elif i + 1 > n:
            prev_atr = (prev_atr * (n - 1) + tr) / n
            out[i] = prev_atr
    return out


def atr_prev(atr: list, i: int) -> float | None:
    """ATR ของแท่งก่อนหน้า (ข้อ 5.1: ใช้ค่าจากแท่งก่อนหน้า)"""
    j = i - 1
    return atr[j] if 0 <= j < len(atr) else None


def ema(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < n:
        return out
    k = 2 / (n + 1)
    e = sum(values[:n]) / n
    out[n - 1] = e
    for i in range(n, len(values)):
        e = values[i] * k + e * (1 - k)
        out[i] = e
    return out


def find_swings(bars: list[Bar], left: int = 2, right: int = 2) -> list[Swing]:
    """จุดสูง/ต่ำกว่า left แท่งซ้ายและ right แท่งขวา (ราคาเท่ากัน: ฝั่งซ้ายต้องต่ำกว่าเคร่งครัด ฝั่งขวาเท่ากันได้ → เลือกจุดที่เกิดก่อน)"""
    out = []
    for i in range(left, len(bars) - right):
        h, l = bars[i].h, bars[i].l
        if all(bars[i - k].h < h for k in range(1, left + 1)) and all(bars[i + k].h <= h for k in range(1, right + 1)):
            out.append(Swing(i, h, "high", i + right, bars[i].t))
        if all(bars[i - k].l > l for k in range(1, left + 1)) and all(bars[i + k].l >= l for k in range(1, right + 1)):
            out.append(Swing(i, l, "low", i + right, bars[i].t))
    return out


def confirmed(swings: list[Swing], upto_idx: int, kind: str | None = None) -> list[Swing]:
    return [s for s in swings if s.confirm_idx <= upto_idx and (kind is None or s.kind == kind)]


def bias(swings: list[Swing], upto_idx: int) -> str:
    """'up' / 'down' / 'neutral' จาก Swing 2 จุดล่าสุดที่ยืนยันแล้ว"""
    hs = confirmed(swings, upto_idx, "high")[-2:]
    ls = confirmed(swings, upto_idx, "low")[-2:]
    if len(hs) < 2 or len(ls) < 2:
        return "neutral"
    if hs[1].price > hs[0].price and ls[1].price > ls[0].price:
        return "up"
    if hs[1].price < hs[0].price and ls[1].price < ls[0].price:
        return "down"
    return "neutral"


def index_at(bars: list[Bar], t_close_limit: float, tf_seconds: int) -> int:
    """index ของแท่งสุดท้ายที่ปิดก่อนหรือเท่ากับ t_close_limit (-1 ถ้าไม่มี)"""
    opens = [b.t for b in bars]
    return bisect_right(opens, t_close_limit - tf_seconds) - 1


def d1_state(d1: list[Bar], d1_swings: list[Swing], ema50: list, upto_idx: int, side: str) -> str:
    """'support' / 'opposite' / 'neutral' (ข้อ 5.1)"""
    if upto_idx < 0 or ema50[upto_idx] is None:
        return "neutral"
    st = bias(d1_swings, upto_idx)
    above = d1[upto_idx].c > ema50[upto_idx]
    want_up = side == "long"
    same_struct = (st == "up") if want_up else (st == "down")
    opp_struct = (st == "down") if want_up else (st == "up")
    same_ema = above if want_up else not above
    if same_struct and same_ema:
        return "support"
    if opp_struct and not same_ema:
        return "opposite"
    return "neutral"


def h4_leg(h4: list[Bar], swings: list[Swing], upto_idx: int, side: str):
    """ขาราคา H4: long = จาก Swing Low ล่าสุดที่ยืนยัน ถึงจุดสูงสุดหลังจากนั้น
    คืน (origin_price, origin_idx, extreme_price, extreme_idx) หรือ None"""
    kind = "low" if side == "long" else "high"
    sw = confirmed(swings, upto_idx, kind)
    if not sw:
        return None
    o = sw[-1]
    rng = h4[o.idx + 1: upto_idx + 1]
    if not rng:
        return None
    if side == "long":
        ext_i = max(range(len(rng)), key=lambda k: rng[k].h)
        return o.price, o.idx, rng[ext_i].h, o.idx + 1 + ext_i
    ext_i = min(range(len(rng)), key=lambda k: rng[k].l)
    return o.price, o.idx, rng[ext_i].l, o.idx + 1 + ext_i


def find_fvgs(bars: list[Bar], atr: list, min_atr: float, start: int = 2, end: int | None = None) -> list[FVG]:
    out = []
    end = len(bars) - 1 if end is None else end
    for k in range(max(2, start), end + 1):
        a = atr_prev(atr, k)
        if a is None:
            continue
        b1, b3 = bars[k - 2], bars[k]
        if b1.h < b3.l and (b3.l - b1.h) >= min_atr * a:
            out.append(FVG(k, b3.l, b1.h, "bull", b1.t, b3.t))
        if b1.l > b3.h and (b1.l - b3.h) >= min_atr * a:
            out.append(FVG(k, b1.l, b3.h, "bear", b1.t, b3.t))
    return out


def displacement_group(bars: list[Bar], atr: list, end_idx: int, side: str, cfg: dict):
    """หากลุ่มแท่ง 1–3 แท่งที่จบที่ end_idx และผ่านเกณฑ์ Displacement คืน start_idx หรือ None"""
    a = atr_prev(atr, end_idx - (cfg["max_bars"] - 1)) or atr_prev(atr, end_idx)
    if a is None:
        return None
    for n in range(1, cfg["max_bars"] + 1):
        s = end_idx - n + 1
        if s < 1:
            break
        grp = bars[s:end_idx + 1]
        ok_dir = all((b.c > b.o) if side == "long" else (b.c < b.o) for b in grp)
        if not ok_dir:
            break
        ok_body = all((b.h - b.l) > 0 and abs(b.c - b.o) / (b.h - b.l) >= cfg["min_body_ratio"] for b in grp)
        move = (grp[-1].c - grp[0].o) if side == "long" else (grp[0].o - grp[-1].c)
        hi, lo = max(b.h for b in grp), min(b.l for b in grp)
        rng = hi - lo
        if rng <= 0:
            continue
        frac = cfg["close_outer_frac"]
        ok_close = (grp[-1].c >= hi - frac * rng) if side == "long" else (grp[-1].c <= lo + frac * rng)
        if ok_body and ok_close and move >= cfg["min_move_atr"] * a:
            return s
    return None


def untouched_after(bars: list[Bar], from_idx: int, price: float, side: str, to_idx: int | None = None) -> bool:
    """ราคายังไม่เคยแตะ price หลังแท่ง from_idx (long: low ยังไม่ลงถึง price)"""
    to_idx = len(bars) - 1 if to_idx is None else to_idx
    for b in bars[from_idx + 1: to_idx + 1]:
        if (side == "long" and b.l <= price) or (side == "short" and b.h >= price):
            return False
    return True
