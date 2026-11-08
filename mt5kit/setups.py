"""รูปแบบ A (ย่อตามเทรนด์) และ B (กวาดสภาพคล่องช่วงตลาดเปิด) — ข้อ 5.2–5.5 ของ plan.md"""
from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field

from . import news as newsmod
from . import structure as st
from .adapters import Bar
from .timeutil import in_window, local_date, window_bounds_utc

H1 = 3600
H4 = 14400
D1 = 86400
W1 = 604800


@dataclass
class Context:
    """ข้อมูลที่คำนวณไว้ล่วงหน้าของ 1 คู่เงิน"""
    symbol: str
    ccys: tuple
    w1: list
    d1: list
    h4: list
    h1: list
    cfg: dict
    events: list = field(default_factory=list)

    def __post_init__(self):
        c = self.cfg
        sw = c["swing"]
        self.h1_atr = st.atr_wilder(self.h1, c["atr_period"])
        self.h4_atr = st.atr_wilder(self.h4, c["atr_period"])
        self.h1_sw = st.find_swings(self.h1, sw["left"], sw["right"])
        self.h4_sw = st.find_swings(self.h4, sw["left"], sw["right"])
        self.d1_sw = st.find_swings(self.d1, sw["left"], sw["right"])
        self.d1_ema = st.ema([b.c for b in self.d1], c["ema_d1"])
        self.h4_fvg = st.find_fvgs(self.h4, self.h4_atr, c["fvg_min_atr"])
        self.h1_fvg = st.find_fvgs(self.h1, self.h1_atr, c["fvg_min_atr"])
        self._h1_t = [b.t for b in self.h1]

    def h4_idx(self, t_close):
        return st.index_at(self.h4, t_close, H4)

    def d1_idx(self, t_close):
        return st.index_at(self.d1, t_close, D1)

    def w1_idx(self, t_close):
        return st.index_at(self.w1, t_close, W1) if self.w1 else -1

    def h1_index_from(self, t):
        """index ของแท่ง H1 แรกที่เปิดตั้งแต่เวลา t"""
        return bisect_left(self._h1_t, t)


@dataclass
class Signal:
    symbol: str
    side: str
    setup: str                  # "A" / "B" / "A+B"
    bos_idx: int
    bos_t: float                # เวลาเปิดแท่ง BOS (UTC)
    disp_start: int
    window: str
    h4_bias: str
    d1_state: str
    entry: float
    entry_from_fvg: bool
    zone_idx: int               # แท่งที่ทำให้โซนยืนยัน
    sl_anchor: float
    sl_anchor_idx: int
    bos_level: float
    atr_h1: float
    fvg: dict | None
    sweep: dict | None
    pullback: dict | None
    targets: list
    tier2_in_signal: bool
    disp_move_atr: float
    fresh: bool = True
    notes: list = field(default_factory=list)

    @property
    def bos_close_t(self):
        return self.bos_t + H1


def _window_name(t_close: float, cfg: dict) -> str | None:
    s = cfg["sessions"]
    if in_window(t_close, s["london"]):
        return "london"
    if in_window(t_close, s["newyork"]):
        return "newyork"
    return None


def liquidity_levels(ctx: Context, t: float) -> dict:
    """ระดับสภาพคล่องที่รู้แล้ว ณ เวลา t: กรอบเอเชียวันนี้, PDH/PDL, PWH/PWL"""
    out = {}
    s = ctx.cfg["sessions"]["asian_range"]
    day = local_date(t, s["tz"])
    a, b = window_bounds_utc(day, s)
    if t >= b:
        bars = [x for x in ctx.h1 if a <= x.t < b]
        if len(bars) >= 4:
            out["ASIA_H"] = (max(x.h for x in bars), b)
            out["ASIA_L"] = (min(x.l for x in bars), b)
    di = ctx.d1_idx(t)
    if di >= 0:
        d = ctx.d1[di]
        out["PDH"] = (d.h, d.t + D1)
        out["PDL"] = (d.l, d.t + D1)
    wi = ctx.w1_idx(t)
    if wi >= 0:
        w = ctx.w1[wi]
        out["PWH"] = (w.h, w.t + W1)
        out["PWL"] = (w.l, w.t + W1)
    return out


def _first_break(h1, from_idx, to_idx, level, thr, side) -> bool:
    """แท่ง to_idx เป็นแท่งแรกที่ปิดทะลุ level ตั้งแต่ from_idx"""
    for b in h1[from_idx:to_idx]:
        if (side == "long" and b.c > level + thr) or (side == "short" and b.c < level - thr):
            return False
    b = h1[to_idx]
    return (b.c > level + thr) if side == "long" else (b.c < level - thr)


def _entry(ctx: Context, s: int, i: int, side: str):
    want = "bull" if side == "long" else "bear"
    last = len(ctx.h1) - 1
    for f in ctx.h1_fvg:
        if f.direction == want and s + 1 <= f.idx3 <= min(i + 1, last):
            return f.mid, True, f.idx3, {"top": f.top, "bottom": f.bottom, "t1": f.t1, "t3": f.t3, "idx3": f.idx3}
    grp = ctx.h1[s:i + 1]
    hi, lo = max(b.h for b in grp), min(b.l for b in grp)
    return (lo + hi) / 2, False, i, None


def _targets(ctx: Context, side: str, h4i: int, t: float, levels: dict, setup: str) -> list:
    out = []
    kind = "high" if side == "long" else "low"
    for sw in st.confirmed(ctx.h4_sw, h4i, kind)[-12:]:
        out.append({"name": "Swing H4", "price": sw.price, "t": sw.t})
    for nm in (("PDH", "PWH", "ASIA_H") if side == "long" else ("PDL", "PWL", "ASIA_L")):
        if nm in levels and (not nm.startswith("ASIA") or "B" in setup):
            out.append({"name": nm, "price": levels[nm][0], "t": None})
    return out


def _setup_a(ctx, i, side, h4i, s, thr):
    leg = st.h4_leg(ctx.h4, ctx.h4_sw, h4i, side)
    if not leg:
        return None
    origin, oidx, ext, eidx = leg
    h4_start = ctx.h1_index_from(ctx.h4[eidx].t)
    h4_end = ctx.h1_index_from(ctx.h4[eidx].t + H4)
    inside = ctx.h1[h4_start:h4_end]
    if not inside:
        return None
    # แท่ง H1 ที่เป็นจุดสุดขาของ H4 → การย่อเริ่มหลังแท่งนี้
    if side == "long":
        pb_start = h4_start + max(range(len(inside)), key=lambda k: inside[k].h)
    else:
        pb_start = h4_start + min(range(len(inside)), key=lambda k: inside[k].l)
    if pb_start >= s - 1:
        return None
    seg = ctx.h1[pb_start:i + 1]
    if side == "long":
        pb_ext = min(b.l for b in seg)
        if pb_ext <= origin:
            return None
        anchor_idx = pb_start + min(range(len(seg)), key=lambda k: seg[k].l)
        half = origin + 0.5 * (ext - origin)
        in_discount = pb_ext <= half
    else:
        pb_ext = max(b.h for b in seg)
        if pb_ext >= origin:
            return None
        anchor_idx = pb_start + max(range(len(seg)), key=lambda k: seg[k].h)
        half = origin - 0.5 * (origin - ext)
        in_discount = pb_ext >= half
    zone = "discount" if in_discount else None
    h4fvg = None
    if not zone:
        want = "bull" if side == "long" else "bear"
        for f in ctx.h4_fvg:
            if f.direction == want and oidx < f.idx3 <= eidx and st.untouched_after(ctx.h4, f.idx3, f.top if side == "long" else f.bottom, side, eidx):
                hit = (pb_ext <= f.top) if side == "long" else (pb_ext >= f.bottom)
                if hit:
                    zone, h4fvg = "h4_fvg", {"top": f.top, "bottom": f.bottom, "t1": f.t1, "t3": f.t3}
                    break
    if not zone:
        return None
    kind = "high" if side == "long" else "low"
    cands = [w for w in st.confirmed(ctx.h1_sw, i - 1, kind) if w.idx > pb_start]
    if not cands:
        return None
    lvl = cands[-1]
    if not _first_break(ctx.h1, lvl.idx + 1, i, lvl.price, thr, side):
        return None
    return {
        "bos_level": lvl.price, "bos_swing_t": lvl.t, "anchor": pb_ext, "anchor_idx": anchor_idx,
        "pullback": {"zone": zone, "leg_origin": origin, "leg_origin_t": ctx.h4[oidx].t, "leg_extreme": ext,
                     "leg_extreme_t": ctx.h4[eidx].t, "half": half, "h4_fvg": h4fvg},
    }


def _setup_b(ctx, i, side, s, thr, atr):
    c = ctx.cfg["sweep"]
    kind = "high" if side == "long" else "low"
    best = None
    jmin = max(1, i - c["bos_within_bars"] - c["reclaim_within_bars"])
    for j in range(i - 1, jmin - 1, -1):
        bj = ctx.h1[j]
        if not _window_name(bj.t + H1, ctx.cfg):
            continue
        levels = liquidity_levels(ctx, bj.t)
        names = ("ASIA_L", "PDL", "PWL") if side == "long" else ("ASIA_H", "PDH", "PWH")
        for nm in names:
            if nm not in levels:
                continue
            L, known_t = levels[nm]
            pierce = (L - bj.l) if side == "long" else (bj.h - L)
            if pierce < c["min_pierce_atr"] * atr:
                continue
            # ต้องเป็นการกวาดครั้งแรกหลังระดับเป็นที่รู้จัก
            k0 = ctx.h1_index_from(known_t)
            prior = ctx.h1[k0:j]
            if any((b.l < L) if side == "long" else (b.h > L) for b in prior):
                continue
            k = None
            for kk in range(j, min(j + c["reclaim_within_bars"], i)):
                if (ctx.h1[kk].c > L) if side == "long" else (ctx.h1[kk].c < L):
                    k = kk
                    break
            if k is None or i - k > c["bos_within_bars"] or s < j:
                continue
            cands = [w for w in st.confirmed(ctx.h1_sw, j - 1, kind)]
            if not cands:
                continue
            lvl = cands[-1]
            if not _first_break(ctx.h1, k, i, lvl.price, thr, side):
                continue
            seg = ctx.h1[j:i + 1]
            if side == "long":
                anchor = min(b.l for b in seg)
                anchor_idx = j + min(range(len(seg)), key=lambda q: seg[q].l)
            else:
                anchor = max(b.h for b in seg)
                anchor_idx = j + max(range(len(seg)), key=lambda q: seg[q].h)
            best = {"bos_level": lvl.price, "bos_swing_t": lvl.t, "anchor": anchor, "anchor_idx": anchor_idx,
                    "sweep": {"level_name": nm, "level": L, "idx": j, "t": bj.t, "reclaim_idx": k, "reclaim_t": ctx.h1[k].t},
                    "levels": levels}
            return best
    return best


def detect(ctx: Context, i_from: int, i_to: int | None = None) -> list[Signal]:
    """หาสัญญาณที่แท่ง BOS อยู่ระหว่าง i_from..i_to (index ของ H1)"""
    cfg = ctx.cfg
    h1 = ctx.h1
    i_to = len(h1) - 1 if i_to is None else i_to
    out: list[Signal] = []
    for i in range(max(i_from, 20), i_to + 1):
        t_close = h1[i].t + H1
        win = _window_name(t_close, cfg)
        if not win:
            continue
        atr = st.atr_prev(ctx.h1_atr, i)
        if not atr:
            continue
        h4i, d1i = ctx.h4_idx(t_close), ctx.d1_idx(t_close)
        if h4i < 10 or d1i < cfg["ema_d1"]:
            continue
        h4b = st.bias(ctx.h4_sw, h4i)
        thr = cfg["bos_min_atr"] * atr
        for side in ("long", "short"):
            d1s = st.d1_state(ctx.d1, ctx.d1_sw, ctx.d1_ema, d1i, side)
            match = (h4b == "up" and side == "long") or (h4b == "down" and side == "short")
            allow_a = match and d1s != "opposite"
            allow_b = allow_a or (h4b == "neutral" and d1s == "support")
            if not (allow_a or allow_b):
                continue
            s = st.displacement_group(h1, ctx.h1_atr, i, side, cfg["displacement"])
            if s is None:
                continue
            ra = _setup_a(ctx, i, side, h4i, s, thr) if allow_a else None
            rb = _setup_b(ctx, i, side, s, thr, atr) if allow_b else None
            if not ra and not rb:
                continue
            # ข่าว Tier 1 ในแท่ง BOS หรือแท่ง Sweep → ไม่นับ
            if newsmod.bar_contains_event(h1[i].t, H1, ctx.events, ctx.ccys, 1):
                continue
            if rb and newsmod.bar_contains_event(h1[rb["sweep"]["idx"]].t, H1, ctx.events, ctx.ccys, 1):
                rb = None
                if not ra:
                    continue
            if ra and rb:
                narrower = ra if ((ra["anchor"] > rb["anchor"]) if side == "long" else (ra["anchor"] < rb["anchor"])) else rb
                setup, chosen = "A+B", narrower
            elif ra:
                setup, chosen = "A", ra
            else:
                setup, chosen = "B", rb
            entry, from_fvg, zidx, fvg = _entry(ctx, s, i, side)
            levels = (rb or {}).get("levels") or liquidity_levels(ctx, h1[i].t)
            grp = h1[s:i + 1]
            move = (grp[-1].c - grp[0].o) if side == "long" else (grp[0].o - grp[-1].c)
            t2 = any(newsmod.bar_contains_event(b.t, H1, ctx.events, ctx.ccys, 2) for b in grp)
            sig = Signal(
                symbol=ctx.symbol, side=side, setup=setup, bos_idx=i, bos_t=h1[i].t, disp_start=s, window=win,
                h4_bias=h4b, d1_state=d1s, entry=entry, entry_from_fvg=from_fvg, zone_idx=zidx,
                sl_anchor=chosen["anchor"], sl_anchor_idx=chosen["anchor_idx"], bos_level=chosen["bos_level"],
                atr_h1=atr, fvg=fvg, sweep=(rb or {}).get("sweep"), pullback=(ra or {}).get("pullback"),
                targets=_targets(ctx, side, h4i, h1[i].t, levels, setup), tier2_in_signal=t2,
                disp_move_atr=move / atr,
            )
            sig.fresh = st.untouched_after(h1, zidx, entry, side)
            out.append(sig)
    # คู่เดียวกัน ทิศเดียวกัน → เก็บสัญญาณล่าสุด
    latest: dict = {}
    for sg in out:
        latest[(sg.symbol, sg.side)] = sg
    return sorted(latest.values(), key=lambda x: x.bos_t)


def detect_live(ctx: Context) -> list[Signal]:
    n = len(ctx.h1)
    return detect(ctx, n - ctx.cfg["max_signal_age_bars"], n - 1)


def scan_row(ctx: Context, now: float) -> dict:
    """ข้อมูลสรุปของคู่เงินสำหรับตารางสแกน (แม้ไม่มีสัญญาณ)"""
    t_close = ctx.h1[-1].t + H1 if ctx.h1 else now
    h4i, d1i = ctx.h4_idx(t_close), ctx.d1_idx(t_close)
    h4b = st.bias(ctx.h4_sw, h4i) if h4i >= 0 else "neutral"
    side = "long" if h4b == "up" else "short" if h4b == "down" else None
    d1s_long = st.d1_state(ctx.d1, ctx.d1_sw, ctx.d1_ema, d1i, "long")
    d1s_short = st.d1_state(ctx.d1, ctx.d1_sw, ctx.d1_ema, d1i, "short")
    zone_note = ""
    if side:
        leg = st.h4_leg(ctx.h4, ctx.h4_sw, h4i, side)
        if leg:
            origin, _, ext, _ = leg
            half = origin + 0.5 * (ext - origin)
            px = ctx.h1[-1].c
            inz = (px <= half) if side == "long" else (px >= half)
            zone_note = "ราคาอยู่ในโซนย่อของขา H4" if inz else "ยังไม่ย่อถึงครึ่งขา H4"
    return {"symbol": ctx.symbol, "h4_bias": h4b, "d1_long": d1s_long, "d1_short": d1s_short,
            "bias_side": side, "zone_note": zone_note, "last_close": ctx.h1[-1].c if ctx.h1 else None}
