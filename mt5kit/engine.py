"""ขั้นตอนหลักของ mt5-swing-analyze: อ่านข้อมูล → ดูแลไม้เดิม → สแกน → วางแผน → จัดอันดับ → Report JSON"""
from __future__ import annotations

import json
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import adapters as ad
from . import news as newsmod
from . import risk as rk
from . import structure as st
from .cot import summary as cot_summary
from .datasource import server_now_from_time_info
from .ledger import Ledger, make_plan_id
from .scoring import score as score_fn
from .setups import H1, Context, detect_live, liquidity_levels, scan_row
from .timeutil import ServerClock, fmt_local, iso, next_local_time_utc, trading_day_key
from .watch import chart_watch, watch_for

UTC = timezone.utc
PKG = Path(__file__).parent


def load_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def load_strategy() -> dict:
    return load_json(PKG / "config" / "strategy-v2.json")


def load_profile(root: Path) -> tuple[dict, bool]:
    p = root / "account-profile.json"
    if p.exists():
        return load_json(p), True
    return load_json(PKG / "config" / "account-profile.example.json"), False


def resolve_symbols(wanted: list[str], available: list[str], profile: dict, aliases: dict | None = None) -> dict:
    """จับคู่ชื่อมาตรฐานกับชื่อจริงของโบรกเกอร์ ห้ามเดาเมื่อมีหลายตัวเลือก"""
    out = {}
    ov = profile.get("symbol_overrides", {})
    sfx = profile.get("symbol_suffix", "")
    for w in wanted:
        if w in ov:
            out[w] = {"name": ov[w], "ok": ov[w] in available or not available, "why": "override"}
            continue
        names = [w, *(aliases or {}).get(w, [])]
        preferred = next((name + sfx for name in names if name + sfx in available), None)
        if preferred:
            out[w] = {"name": preferred, "ok": True, "why": "suffix"}
            continue
        cands = [a for a in available if any(a.upper().startswith(name.upper()) for name in names)]
        if len(cands) == 1:
            out[w] = {"name": cands[0], "ok": True, "why": "unique"}
        elif w in cands:
            out[w] = {"name": w, "ok": True, "why": "exact"}
        else:
            out[w] = {"name": None, "ok": False, "why": "ไม่พบ" if not cands else f"มีหลายชื่อ {cands[:4]} — ตั้ง symbol_overrides"}
    return out


def fill_tick_values(symbols: dict, acct_ccy: str, limitations: list, required_symbols: set | None = None):
    """MT5 อาจคืน tick_value = 0 ตอนตลาดปิด → คำนวณจาก contract size และราคาคู่ที่เกี่ยวข้อง (ค่าประมาณ)"""
    def px(base, quote):
        for name, s in symbols.items():
            if s.base == base and s.quote == quote and s.bid:
                return s.bid
        return None
    fixed = []
    for name, s in symbols.items():
        if required_symbols is not None and name not in required_symbols:
            continue
        if s.tick_value or not s.contract_size:
            continue
        ts = s.tick_size or s.point
        if not ts:
            continue
        q = s.quote
        if q == acct_ccy:
            tv = ts * s.contract_size
        elif px(acct_ccy, q):
            tv = ts * s.contract_size / px(acct_ccy, q)
        elif px(q, acct_ccy):
            tv = ts * s.contract_size * px(q, acct_ccy)
        else:
            continue
        s.tick_size = ts
        s.tick_value = s.tick_value_loss = tv
        s.missing = [f for f in s.missing if f not in ("tick_value", "tick_size")]
        fixed.append(name)
    if fixed:
        limitations.append("MT5 คืน tick_value = 0 — คำนวณเงินต่อ lot จาก contract size และราคาคู่อ้างอิงแทน (ค่าประมาณ): " + ", ".join(sorted(fixed)))


def _round(sym, p, mode="nearest"):
    return rk.round_price(sym, p, mode)


def _median_spread_same_hour(h1: list, hour_utc: int, days: int = 20):
    sp = [b.spread for b in h1[-days * 24 - 24:] if b.spread and datetime.fromtimestamp(b.t, UTC).hour == hour_utc]
    return statistics.median(sp) if len(sp) >= 5 else None


def build_plan(sig, ctx: Context, sym: ad.SymbolInfo, acct: ad.Account, ex: rk.Exposure, now: float,
               cfg: dict, profile: dict, ddm: float, macro: str, events: list, day_key: str) -> dict:
    side, atr = sig.side, sig.atr_h1
    checks: list[dict] = []

    def chk(name, ok, detail=""):
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        return ok

    c_sl = cfg["sl"]
    spread = sym.spread
    tick = sym.tick_size or sym.point
    buffer = max(c_sl["buffer_atr"] * atr, c_sl["buffer_spread_mult"] * spread, sym.stops_level * sym.point + c_sl["buffer_extra_ticks"] * tick)
    entry = _round(sym, sig.entry)
    if side == "long":
        sl = sig.sl_anchor - buffer
        if entry - sl < c_sl["min_dist_atr"] * atr:
            sl = entry - c_sl["min_dist_atr"] * atr
        sl = _round(sym, sl, "down")
    else:
        sl = sig.sl_anchor + buffer
        if sl - entry < c_sl["min_dist_atr"] * atr:
            sl = entry + c_sl["min_dist_atr"] * atr
        sl = _round(sym, sl, "up")
    dist = abs(entry - sl)
    chk("ระยะ SL ไม่เกิน 3×ATR(H1)", dist <= c_sl["max_dist_atr"] * atr, f"{dist / atr:.2f}×ATR")

    # ราคาหลัง BOS
    after = ctx.h1[sig.bos_idx + 1:]
    hi_after = max([b.h for b in after], default=ctx.h1[sig.bos_idx].h)
    lo_after = min([b.l for b in after], default=ctx.h1[sig.bos_idx].l)
    reached = hi_after if side == "long" else lo_after

    # TP: ระดับที่ใกล้ที่สุดที่ยังไม่ถูกแตะ
    tps = [t for t in sig.targets if ((t["price"] > max(entry, reached)) if side == "long" else (t["price"] < min(entry, reached)))]
    tp_t = min(tps, key=lambda t: abs(t["price"] - entry)) if tps else None
    tp = _round(sym, tp_t["price"], "down" if side == "long" else "up") if tp_t else None
    chk("มีระดับเป้าหมาย (TP)", tp is not None, tp_t["name"] if tp_t else "ไม่มีระดับเป้าหมายที่ยังไม่ถูกแตะ")

    chk("โซนเข้ายังใหม่ (ราคายังไม่ย้อนมาแตะ)", sig.fresh)
    invalid_before = (lo_after <= sl) if side == "long" else (hi_after >= sl)
    chk("ราคายังไม่หลุด SL ก่อนเข้า", not invalid_before)

    # ราคาและข้อกำหนดโบรกเกอร์
    order_type = "buy_limit" if side == "long" else "sell_limit"
    if side == "long":
        lim_ok = sym.ask > 0 and entry < sym.ask - sym.stops_level * sym.point
    else:
        lim_ok = sym.bid > 0 and entry > sym.bid + sym.stops_level * sym.point
    chk("ราคาเข้าเป็น Limit ที่ถูกต้องเทียบราคาปัจจุบัน", lim_ok, f"bid {sym.bid} ask {sym.ask}")
    chk("ข้อมูล Symbol ครบ", not sym.missing, ", ".join(sym.missing))
    chk("Symbol ซื้อขายได้", sym.trade_allowed)

    # อายุ Pending
    ex_cfg = cfg["expiry"]
    exp = sig.bos_close_t + ex_cfg["hours_after_bos"] * 3600
    exp = min(exp, next_local_time_utc(now, ex_cfg["daily_cutoff"]["tz"], ex_cfg["daily_cutoff"]["time"]))
    exp = min(exp, next_local_time_utc(now, ex_cfg["friday_cutoff"]["tz"], ex_cfg["friday_cutoff"]["time"], weekday=4))
    ncfg = cfg["news"]
    blk = newsmod.blocked_now(now, events, ctx.ccys, ncfg)
    chk("ไม่อยู่ในช่วงงดข่าว Tier 1", blk is None, f"{blk.currency} {blk.name}" if blk else "")
    nxt = newsmod.next_tier1_start(now, events, ctx.ccys, ncfg)
    if nxt and nxt[0] < exp:
        exp = nxt[0]
    t2 = newsmod.tier2_conflict(now, exp, events, ctx.ccys, ncfg["tier2_window_min"])
    if t2:
        exp = min(exp, t2.t - ncfg["tier2_window_min"] * 60)
    chk("Pending มีอายุอย่างน้อย 30 นาที", exp - now >= 1800, f"หมดอายุ {fmt_local(exp)}")
    # ข่าว Tier 1 เกิดหลัง BOS → สัญญาณเก่าใช้ไม่ได้
    t1_since = [e for e in newsmod.events_for(events, ctx.ccys, 1) if sig.bos_close_t <= e.t <= now]
    chk("ไม่มีข่าว Tier 1 เกิดหลังสัญญาณ", not t1_since, t1_since[0].name if t1_since else "")

    # Macro
    chk("Macro ไม่ขัดแย้งชัดเจน", macro != "conflict", macro)

    # พอร์ต
    chk("ไม่มีไอเดียเดิมในคู่นี้", sym.name not in ex.symbols)
    chk("ไม่มีไม้ที่คำนวณความเสี่ยงไม่ได้", not ex.unknown, f"{len(ex.unknown)} รายการ")
    chk("จำนวนไอเดียไม่เกินเพดาน", ex.ideas < profile["risk"]["max_concurrent_ideas"], f"{ex.ideas}/{profile['risk']['max_concurrent_ideas']}")

    size = rk.size_position(acct, sym, side, entry, sl, ex, profile, ddm, cfg)
    chk("คำนวณล็อตได้และอยู่ในงบ", size.ok, size.reason)

    rr_net = cost_r = reward_money = None
    if size.ok and tp is not None:
        reward_pl = rk.money_per_lot(sym, tp - entry, loss=False)
        rr_net = (reward_pl - size.cost_per_lot) / (size.loss_per_lot + size.cost_per_lot)
        cost_r = size.cost_per_lot / size.loss_per_lot
        reward_money = size.volume * (reward_pl - size.cost_per_lot)
        chk(f"R:R สุทธิ ≥ {cfg['min_rr_net']}", rr_net >= cfg["min_rr_net"], f"{rr_net:.2f}")
        chk("ต้นทุนรวม ≤ 0.15R", cost_r <= cfg["cost"]["max_cost_r"], f"{cost_r:.3f}R")
    chk("Spread ≤ 10% ของระยะ SL", spread <= cfg["cost"]["max_spread_frac_of_sl"] * dist, f"{spread / dist:.1%}" if dist else "")
    med = _median_spread_same_hour(ctx.h1, datetime.fromtimestamp(now, UTC).hour)
    if med and sym.point:
        cur_pts = spread / sym.point
        chk("Spread ไม่สูงผิดปกติ (≤ 2× มัธยฐาน)", cur_pts <= 2 * med, f"{cur_pts:.0f} vs มัธยฐาน {med:.0f} point")

    margin_new, mnote = (None, "")
    if size.ok:
        margin_new, mnote = rk.approx_margin(sym, acct, size.volume, entry)
        if margin_new is not None:
            frac = (acct.margin + margin_new) / acct.equity if acct.equity else 1
            chk("มาร์จินหลัง Fill ≤ 20% ของ Equity (ประมาณ)", frac <= profile["risk"]["max_margin_frac_after_fill"], f"{frac:.1%} ({mnote})")

    bos_hhmm = datetime.fromtimestamp(sig.bos_t, UTC).strftime("%H%M")
    plan_id = make_plan_id(day_key, sym.name, side, sig.setup, bos_hhmm)
    sc = score_fn(sig, rr_net, cost_r, macro, cfg)
    stress = None
    if size.ok and ctx.d1:
        d1_atr = st.atr_wilder(ctx.d1, cfg["atr_period"])[-1]
        if d1_atr:
            per_lot_1x = rk.money_per_lot(sym, d1_atr)
            stress = {"atr_d1": d1_atr, "loss_1x": per_lot_1x * size.volume, "loss_2x": 2 * per_lot_1x * size.volume}
    return {
        "plan_id": plan_id, "revision": 1, "symbol": sym.name, "std_symbol": ctx.symbol, "side": side, "digits": sym.digits,
        "order_type": order_type, "setup": sig.setup, "window": sig.window,
        "entry": entry, "sl": sl, "tp": tp, "tp_name": tp_t["name"] if tp_t else None,
        "expiry_utc": exp, "expiry_iso": iso(exp),
        "volume": size.volume if size.ok else 0.0, "risk_money": size.risk_money if size.ok else 0.0,
        "risk_pct": (size.risk_money / min(acct.balance, acct.equity)) if size.ok else 0.0,
        "loss_per_lot": size.loss_per_lot, "cost_per_lot": size.cost_per_lot, "budget": size.budget,
        "reward_money": reward_money, "rr_net": rr_net, "cost_r": cost_r, "margin_est": margin_new,
        "stress": stress, "score": sc, "macro": macro,
        "checks": checks, "passed": all(c["ok"] for c in checks), "notes": size.notes + sig.notes,
        "signal": {
            "bos_t": sig.bos_t, "bos_level": sig.bos_level, "disp_start_t": ctx.h1[sig.disp_start].t,
            "disp_move_atr": round(sig.disp_move_atr, 2), "atr_h1": atr, "h4_bias": sig.h4_bias, "d1_state": sig.d1_state,
            "fvg": sig.fvg, "entry_from_fvg": sig.entry_from_fvg, "sweep": sig.sweep, "pullback": sig.pullback,
            "sl_anchor": sig.sl_anchor, "sl_anchor_t": ctx.h1[sig.sl_anchor_idx].t, "tier2_in_signal": sig.tier2_in_signal,
            "targets": sig.targets,
        },
        "invalidation": _invalidation_text(side, sl, tp, exp),
        "status": "CANDIDATE",
    }


def _invalidation_text(side, sl, tp, exp):
    a = "ต่ำกว่า" if side == "long" else "สูงกว่า"
    b = "สูงกว่า" if side == "long" else "ต่ำกว่า"
    return [f"ราคา{a} {sl} ก่อนเข้า", f"ราคาถึง {tp} ({b}) ก่อน Pending ทำงาน" if tp else "ไม่มี TP", f"ไม่ Fill ภายใน {fmt_local(exp)} (เวลาไทย)", "H4 เปลี่ยนทิศทาง หรือมีข่าว Tier 1 ใกล้เข้ามา"]


def manage_existing(positions, orders, ledger: Ledger, contexts: dict, symbols: dict, now: float, cfg: dict, profile: dict) -> list[dict]:
    out = []
    owned = ledger.owned_tickets()
    ex_cfg = profile.get("execution", {})
    m = cfg["management"]

    def owner_of(ticket, magic, comment):
        if str(ticket) in owned:
            return "skill", owned[str(ticket)]
        # MT5 MCP ไม่รองรับ magic → ยอมรับ comment ขึ้นต้นด้วย prefix ของสกิล (ร่วมกับ ledger)
        prefix = ex_cfg.get("comment_prefix", "nsl") + " "
        if str(comment).startswith(prefix) and (magic in (None, 0) or magic == ex_cfg.get("magic")):
            return "skill", None
        return "other", None

    std_by_name = {}
    for std, ctx in contexts.items():
        std_by_name[ctx.broker_name] = ctx
    for o in orders:
        own, pid = owner_of(o.ticket, o.magic, o.comment)
        row = {"kind": "pending", "ticket": o.ticket, "symbol": o.symbol, "side": o.side, "volume": o.volume,
               "price": o.price, "sl": o.sl, "tp": o.tp, "owner": own, "plan_id": pid, "action": "KEEP", "reason": "ยังตรงแผน"}
        if own != "skill":
            row.update(action="REPORT_ONLY", reason="ไม่ใช่ไม้ของสกิล — นับความเสี่ยงแต่ไม่แก้")
        else:
            ctx = std_by_name.get(o.symbol)
            if ctx:
                h4i = ctx.h4_idx(now)
                b = st.bias(ctx.h4_sw, h4i)
                if (o.side == "long" and b == "down") or (o.side == "short" and b == "up"):
                    row.update(action="CANCEL", reason=f"H4 เปลี่ยนเป็น {b}")
                nxt = newsmod.next_tier1_start(now, ctx.events, ctx.ccys, cfg["news"])
                if nxt and (o.expiry_utc is None or nxt[0] < o.expiry_utc) and nxt[0] - now < 3 * 3600:
                    row.update(action="CANCEL", reason=f"ข่าว Tier 1 ({nxt[1].name}) จะเริ่มก่อน Pending หมดอายุ")
            if o.expiry_utc is None:
                row.update(action="CANCEL", reason="Pending ไม่มีเวลาหมดอายุ")
        out.append(row)
    for p in positions:
        own, pid = owner_of(p.ticket, p.magic, p.comment)
        row = {"kind": "position", "ticket": p.ticket, "symbol": p.symbol, "side": p.side, "volume": p.volume,
               "price": p.price_open, "sl": p.sl, "tp": p.tp, "profit": p.profit, "owner": own, "plan_id": pid,
               "action": "KEEP", "reason": "ยังตรงแผน"}
        if own != "skill":
            row.update(action="REPORT_ONLY", reason="ไม่ใช่ไม้ของสกิล — นับความเสี่ยงแต่ไม่แก้")
            out.append(row)
            continue
        plan = ledger.get_plan(pid) if pid else None
        init_sl = (plan or {}).get("sl") or p.sl
        r_unit = abs(p.price_open - init_sl) if init_sl else None
        ctx = std_by_name.get(p.symbol)
        sym = symbols.get(p.symbol)
        if not r_unit or not ctx or not sym:
            row.update(action="REVIEW", reason="ข้อมูลไม่พอคำนวณ R")
            out.append(row)
            continue
        px = p.price_current or (sym.bid if p.side == "long" else sym.ask)
        r_now = ((px - p.price_open) if p.side == "long" else (p.price_open - px)) / r_unit
        since = [b for b in ctx.h1 if p.time_utc and b.t >= p.time_utc - 3600]
        mfe = max([(b.h - p.price_open) if p.side == "long" else (p.price_open - b.l) for b in since], default=0) / r_unit
        row.update(r_now=round(r_now, 2), mfe_r=round(mfe, 2), bars_held=len(since))
        held_days = (now - p.time_utc) / 86400 if p.time_utc else 0
        if len(since) >= m["time_exit_h1_bars"] and mfe < m["time_exit_min_r"]:
            row.update(action="PROPOSE_CLOSE", reason=f"ถือ {len(since)} แท่ง H1 แล้วยังไม่เคยถึง +{m['time_exit_min_r']}R")
        elif held_days >= m["max_hold_trading_days"] * 7 / 5:
            row.update(action="PROPOSE_CLOSE", reason="ถือครบ 5 วันทำการ")
        else:
            fr = cfg["management"]["friday_review"]
            lt = datetime.fromtimestamp(now, UTC).astimezone(ZoneInfo(fr["tz"]))
            if lt.weekday() == 4 and lt.strftime("%H:%M") >= fr["time"]:
                sl_safe = (p.sl >= p.price_open) if p.side == "long" else (p.sl and p.sl <= p.price_open)
                if r_now < m["friday_keep_min_r"] or not sl_safe:
                    row.update(action="PROPOSE_CLOSE", reason="กฎวันศุกร์: ยังไม่ถึง +1R หรือ SL ยังไม่อยู่ที่ทุน")
            if row["action"] == "KEEP" and mfe >= m["trail_after_r"]:
                kind = "low" if p.side == "long" else "high"
                sws = [w for w in st.confirmed(ctx.h1_sw, len(ctx.h1) - 1, kind) if p.time_utc and w.t >= p.time_utc]
                if sws:
                    atr = st.atr_wilder(ctx.h1, cfg["atr_period"])[-1] or 0
                    buf = max(cfg["sl"]["buffer_atr"] * atr, 2 * sym.spread)
                    new_sl = rk.round_price(sym, sws[-1].price - buf if p.side == "long" else sws[-1].price + buf, "down" if p.side == "long" else "up")
                    better = (new_sl > p.sl) if p.side == "long" else (p.sl == 0 or new_sl < p.sl)
                    valid = (new_sl < px) if p.side == "long" else (new_sl > px)
                    if better and valid:
                        row.update(action="MODIFY_SL", new_sl=new_sl, reason=f"ได้ +{mfe:.1f}R และมี Swing H1 ใหม่ — เลื่อน SL ไปหลัง Swing")
        out.append(row)
    return out


def chart_for(ctx: Context, plan: dict | None, now: float, events: list) -> dict:
    """ข้อมูลกราฟ (เวลา UTC) — render.py จะเลื่อนเป็นเวลาไทย"""
    def candles(bars, n):
        return [[int(b.t), b.o, b.h, b.l, b.c] for b in bars[-n:]]
    out = {"tfs": {"H1": candles(ctx.h1, 150), "H4": candles(ctx.h4, 150), "D1": candles(ctx.d1, 150)}}
    lv = liquidity_levels(ctx, now)
    out["levels"] = [{"name": k, "price": v[0]} for k, v in lv.items() if not k.startswith("ASIA")]
    s = ctx.cfg["sessions"]["asian_range"]
    if "ASIA_H" in lv:
        out["asia"] = {"top": lv["ASIA_H"][0], "bottom": lv["ASIA_L"][0], "t_end": lv["ASIA_H"][1], "t_start": lv["ASIA_H"][1] - 7 * 3600}
    out["news"] = [{"t": e.t, "tier": e.tier, "ccy": e.currency, "name": e.name} for e in newsmod.events_for(events, ctx.ccys)
                   if e.tier in (1, 2) and now - 86400 <= e.t <= now + 2 * 86400]
    if not plan:
        return out
    sg = plan["signal"]
    marks = []
    if sg.get("sweep"):
        marks.append({"t": sg["sweep"]["t"], "pos": "below" if plan["side"] == "long" else "above", "n": 1,
                      "text": f"กวาด {sg['sweep']['level_name']}", "tip": f"แท่งนี้กวาด {sg['sweep']['level_name']} {sg['sweep']['level']:.5g} แล้วปิดกลับเข้ามา"})
    else:
        marks.append({"t": sg["sl_anchor_t"], "pos": "below" if plan["side"] == "long" else "above", "n": 1,
                      "text": "จุดย่อสุด", "tip": "จุดต่ำสุดของการย่อ ใช้วาง SL" if plan["side"] == "long" else "จุดสูงสุดของการดีด ใช้วาง SL"})
    marks.append({"t": sg["bos_t"], "pos": "above" if plan["side"] == "long" else "below", "n": 2,
                  "text": "ปิดทะลุ (BOS)", "tip": f"แท่งแรงปิดทะลุ Swing {sg['bos_level']:.5g} ({sg['disp_move_atr']}×ATR)"})
    if sg.get("fvg"):
        marks.append({"t": sg["fvg"]["t3"], "pos": "below" if plan["side"] == "long" else "above", "n": 3,
                      "text": "FVG", "tip": "ช่องว่างราคาที่ใช้เป็นจุดเข้า"})
    out["markers"] = marks
    boxes = []
    t0 = sg["bos_t"] + 3600
    if sg.get("fvg"):
        boxes.append({"t1": sg["fvg"]["t1"], "t2": plan["expiry_utc"], "top": sg["fvg"]["top"], "bottom": sg["fvg"]["bottom"], "kind": "fvg", "label": "FVG"})
    pb = sg.get("pullback")
    if pb:
        top, bot = (pb["half"], pb["leg_origin"]) if plan["side"] == "long" else (pb["leg_origin"], pb["half"])
        boxes.append({"t1": pb["leg_extreme_t"], "t2": sg["bos_t"], "top": top, "bottom": bot, "kind": "zone",
                      "label": "โซนย่อ (Discount)" if plan["side"] == "long" else "โซนดีด (Premium)"})
        if pb.get("h4_fvg"):
            f = pb["h4_fvg"]
            boxes.append({"t1": f["t1"], "t2": sg["bos_t"], "top": f["top"], "bottom": f["bottom"], "kind": "zone", "label": "FVG H4"})
    if plan.get("tp"):
        boxes.append({"t1": t0, "t2": plan["expiry_utc"] + 24 * 3600, "top": max(plan["entry"], plan["tp"]), "bottom": min(plan["entry"], plan["tp"]), "kind": "reward", "label": ""})
    boxes.append({"t1": t0, "t2": plan["expiry_utc"] + 24 * 3600, "top": max(plan["entry"], plan["sl"]), "bottom": min(plan["entry"], plan["sl"]), "kind": "risk", "label": ""})
    out["boxes"] = boxes
    # ฉากทัศน์: เส้นตรงหักมุมจากราคาล่าสุด → Entry → TP/SL (ไม่ใช่การทำนาย)
    last = ctx.h1[-1]
    tl = last.t + 3600
    atr = sg["atr_h1"] or 1e-9
    t_entry = min(tl + 3 * 3600, plan["expiry_utc"])
    paths = []
    if plan.get("tp"):
        h_tp = max(4, min(72, abs(plan["tp"] - plan["entry"]) / atr * 2.0))
        paths.append({"kind": "win", "label": "ถ้าเป็นตามแผน", "pts": [[tl, last.c], [t_entry, plan["entry"]], [t_entry + h_tp * 3600, plan["tp"]]]})
    h_sl = max(2, min(48, abs(plan["entry"] - plan["sl"]) / atr * 1.5))
    paths.append({"kind": "loss", "label": "ถ้าผิดทาง", "pts": [[tl, last.c], [t_entry, plan["entry"]], [t_entry + h_sl * 3600, plan["sl"]]]})
    out["scenario"] = paths
    out["vlines"] = [{"t": plan["expiry_utc"], "label": "Pending หมดอายุ", "kind": "expiry"}]
    out["bos_level"] = {"price": sg["bos_level"], "t1": sg["bos_t"] - 6 * 3600, "t2": sg["bos_t"] + 3600}
    return out


def select_symbol(symbol: str, cfg: dict) -> str:
    """Accept a configured standard symbol or alias; never extend the strategy universe."""
    requested = symbol.strip().upper().removesuffix("#")
    aliases = cfg.get("symbol_aliases", {})
    matches = [std for std in cfg["symbols"]
               if requested in [std.upper(), *(name.upper() for name in aliases.get(std, []))]]
    if len(matches) != 1:
        raise ValueError("เลือกสินทรัพย์ไม่ถูกต้อง ใช้: " + ", ".join(cfg["symbols"]))
    return matches[0]


def run_scan(root: Path, source, mode: str = "plan_only", slot: str = "auto", now_utc: float | None = None,
             symbol: str | None = None) -> tuple[Path, dict]:
    cfg = load_strategy()
    selected = select_symbol(symbol, cfg) if symbol is not None else None
    wanted = [selected] if selected else cfg["symbols"]
    profile, profile_real = load_profile(root)
    limitations, warnings = [], []
    if not profile_real:
        limitations.append("ใช้ Profile ตัวอย่าง (ยังไม่ได้สร้าง account-profile.json)")

    if now_utc is None:
        now_utc = time.time()
    now = float(now_utc)
    ti = source.time_info()
    srv_now = server_now_from_time_info(ti)
    clock = ServerClock.from_profile(profile.get("server_time"), srv_now, datetime.fromtimestamp(now, UTC))

    acct = ad.parse_account(source.account())
    exp_login = profile.get("expected_account_login")
    if exp_login and str(exp_login) != acct.login:
        raise RuntimeError(f"บัญชีไม่ตรง Profile (คาด {exp_login})")
    positions, orders = ad.parse_positions_orders(source.positions(), clock)

    sym_payload = source.symbols()
    symbols = ad.parse_symbols(sym_payload, clock)
    resolved = resolve_symbols(wanted, list(symbols.keys()), profile, cfg.get("symbol_aliases"))
    need = {r["name"] for r in resolved.values() if r["ok"]} | {p.symbol for p in positions} | {o.symbol for o in orders}
    for name in need:
        s = symbols.get(name)
        if s is None or s.missing:
            extra = source.symbol_info(name)
            if extra:
                got = ad.parse_symbols(extra, clock)
                if name in got:
                    symbols[name] = got[name]
    fill_tick_values(symbols, acct.currency, limitations, need if selected else None)
    ex = rk.portfolio_exposure(positions, orders, symbols)

    ledger = Ledger(root)
    rollover = cfg["sessions"]["trading_day_rollover"]
    day_key = trading_day_key(now, rollover)
    week_key = datetime.fromisoformat(day_key).strftime("%G-W%V")
    eq = ledger.track_equity(day_key, week_key, acct.equity, now)
    ddm, dd_note = rk.dd_multiplier(eq["drawdown"], profile)
    gates = []
    rp = profile["risk"]
    if eq["day_pnl_frac"] <= -rp["daily_loss_stop"]:
        gates.append(f"ขาดทุนวันนี้ {eq['day_pnl_frac']:.2%} ถึงเพดาน — หยุดเปิดใหม่ถึงสิ้นวัน")
    if eq["week_pnl_frac"] <= -rp["weekly_loss_stop"]:
        gates.append(f"ขาดทุนสัปดาห์นี้ {eq['week_pnl_frac']:.2%} ถึงเพดาน — หยุดเปิดใหม่ถึงสัปดาห์หน้า")
    if dd_note:
        (gates if ddm == 0 else warnings).append(dd_note)
    if ex.unknown:
        gates.append(f"มี {len(ex.unknown)} ไม้ที่คำนวณความเสี่ยงไม่ได้ (เช่น ไม่มี SL) — งดเปิดเพิ่ม")
    orders_today = ledger.orders_today(day_key)
    quota = max(0, cfg["frequency"]["max_new_orders_per_day"] - orders_today)

    # ข่าว
    events, news_ok = [], True
    try:
        f_srv = clock.to_server(datetime.fromtimestamp(now - 2 * 86400, UTC))
        t_srv = clock.to_server(datetime.fromtimestamp(now + 3 * 86400, UTC))
        events = newsmod.classify(ad.parse_calendar(source.calendar(f_srv, t_srv), clock))
        if not events:
            news_ok = False
    except Exception as e:  # noqa: BLE001
        news_ok = False
        warnings.append(f"อ่านปฏิทินข่าวไม่ได้: {str(e)[:120]}")
    if not news_ok:
        gates.append("ไม่มีข้อมูลปฏิทินข่าว — งดเปิดไม้ใหม่ (ดูแลไม้เดิมได้)")

    # Macro (โมเดลเขียนไว้วันละครั้ง)
    macro_path = root / "runs" / "macro" / f"{day_key}.json"
    macro = load_json(macro_path) if macro_path.exists() else None
    if not macro:
        limitations.append("ยังไม่มีสรุป Macro ของวันนี้ — ถือว่าเป็นกลางทุกคู่")
    cot_path = root / "runs" / "cache" / "cot.json"
    cot = cot_summary(load_json(cot_path)) if cot_path.exists() else {"available": False, "note": "ยังไม่ได้ดึง COT"}

    contexts, scan_rows, candidates = {}, [], []
    for std, r in resolved.items():
        row = {"symbol": std, "broker": r["name"], "result": "ข้าม", "reason": ""}
        if not r["ok"]:
            row["reason"] = f"Symbol: {r['why']}"
            scan_rows.append(row)
            continue
        name = r["name"]
        try:
            bars = {tf: ad.closed_bars(ad.parse_bars(source.bars(name, tf, cfg["bars"][tf]), clock), tf, now) for tf in ("W1", "D1", "H4", "H1")}
        except Exception as e:  # noqa: BLE001
            row["reason"] = f"BLOCKED_DATA: อ่านแท่งเทียนไม่ได้ ({str(e)[:80]})"
            scan_rows.append(row)
            continue
        if len(bars["H1"]) < 300 or len(bars["H4"]) < 100 or len(bars["D1"]) < cfg["ema_d1"] + 10:
            row["reason"] = f"BLOCKED_DATA: แท่งไม่พอ (H1 {len(bars['H1'])}, H4 {len(bars['H4'])}, D1 {len(bars['D1'])})"
            scan_rows.append(row)
            continue
        sym = symbols.get(name)
        ccys = newsmod.pair_currencies(std, sym.base if sym else None, sym.quote if sym else None)
        ctx = Context(std, ccys, bars["W1"], bars["D1"], bars["H4"], bars["H1"], cfg, events)
        ctx.broker_name = name
        contexts[std] = ctx
        row.update(scan_row(ctx, now))
        stale = now - (bars["H1"][-1].t + H1)
        if stale > 3 * 3600:
            row["stale_hours"] = round(stale / 3600, 1)
        sigs = detect_live(ctx)
        if not sigs:
            row["result"] = "เฝ้าดู" if row.get("bias_side") else "ข้าม"
            row["reason"] = (row.get("zone_note") or "") + (" · ยังไม่มีสัญญาณ A/B" if row.get("bias_side") else "H4 เป็นกลาง")
        for sg in sigs:
            m = ((macro or {}).get("pairs", {}).get(std, {}) or {}).get(sg.side, "neutral")
            if sym is None:
                row["reason"] = "BLOCKED_DATA: ไม่มีข้อมูล Symbol"
                continue
            plan = build_plan(sg, ctx, sym, acct, ex, now, cfg, profile, ddm, m, events, day_key)
            plan["std_symbol"] = std
            candidates.append(plan)
            row["result"] = "ผู้สมัคร"
            row["setup"] = sg.setup
            row["score"] = plan["score"]["total"]
            fails = [c["name"] for c in plan["checks"] if not c["ok"]]
            row["reason"] = ("ผ่านทุกเงื่อนไข" if not fails else "ไม่ผ่าน: " + "; ".join(fails[:2]))
        scan_rows.append(row)

    management = manage_existing(positions, orders, ledger, contexts, symbols, now, cfg, profile)
    if selected:
        selected_broker = resolved[selected]["name"]
        for item in management:
            if item["symbol"] != selected_broker:
                item.update(action="REPORT_ONLY", reason="อยู่นอกสินทรัพย์ที่เลือก — นับความเสี่ยง แต่ไม่ประเมินการจัดการในรอบนี้")
                item.pop("new_sl", None)

    # เลือกแผน
    allow_new = not gates
    eligible = sorted([p for p in candidates if p["passed"] and p["score"]["pass"]],
                      key=lambda p: (-p["score"]["total"], p["cost_r"] or 9))
    chosen = []
    run_ex = ex
    for p in eligible:
        if not allow_new:
            p["status"] = "BLOCKED"
            continue
        if len(chosen) >= quota:
            p["status"] = "READY_BUT_QUOTA"
            continue
        ctx = contexts[p["std_symbol"]]
        sym = symbols[p["symbol"]]
        rs = rk.size_position(acct, sym, p["side"], p["entry"], p["sl"], run_ex, profile, ddm, cfg)
        if not rs.ok or sym.name in run_ex.symbols:
            p["status"] = "REJECTED"
            p["checks"].append({"name": "ผ่านเพดานรวมหลังเลือกไอเดียก่อนหน้า", "ok": False, "detail": rs.reason})
            continue
        if abs(rs.volume - p["volume"]) > 1e-9:
            p["volume"], p["risk_money"] = rs.volume, rs.risk_money
            p["risk_pct"] = rs.risk_money / min(acct.balance, acct.equity)
        p["status"] = "PROPOSED"
        chosen.append(p)
        run_ex.total += rs.risk_money
        run_ex.add_ccy(sym.base, sym.quote, p["side"], rs.risk_money)
        run_ex.symbols.add(sym.name)
        run_ex.ideas += 1
    for p in candidates:
        if p["status"] == "CANDIDATE":
            p["status"] = "REJECTED" if not p["passed"] else "BELOW_SCORE"
    for row in scan_rows:
        for p in candidates:
            if p["std_symbol"] == row["symbol"]:
                row["result"] = {"PROPOSED": "เลือก", "READY_BUT_QUOTA": "ผ่านแต่เต็มโควตา", "BLOCKED": "ผ่านแต่ถูกระงับ",
                                 "BELOW_SCORE": "คะแนนไม่ถึง", "REJECTED": "ไม่ผ่าน"}.get(p["status"], row["result"])

    eff_mode = mode
    if mode == "analyze_and_place" and (gates or not profile.get("execution", {}).get("enabled")):
        eff_mode = "plan_only"
        warnings.append("เปลี่ยนเป็นโหมดแผนอย่างเดียว: " + ("; ".join(gates) if gates else "execution.enabled = false ใน Profile"))

    run_id = datetime.fromtimestamp(now, UTC).strftime("%Y%m%d-%H%M") + f"-{slot}"
    if selected:
        run_id = datetime.fromtimestamp(now, UTC).strftime("%Y%m%d-%H%M%S") + f"-{slot}-{selected.lower()}"
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    reserved_after = ex.total  # run_ex เป็นอ็อบเจ็กต์เดียวกัน → รวมแผนที่เลือกแล้ว
    by_ccy = {k: {"long": round(v["long"], 2), "short": round(v["short"], 2)} for k, v in run_ex.by_ccy.items()}

    if chosen:
        decision = f"เสนอ {len(chosen)} แผน" if eff_mode == "plan_only" else f"จะวาง {len(chosen)} ออเดอร์"
    elif gates:
        decision = "งดเปิดไม้ใหม่ — " + gates[0]
    else:
        decision = "รอ — ยังไม่มีจังหวะที่ผ่านทุกกฎ"

    for p in chosen:
        ledger.put_plan({**{k: v for k, v in p.items() if k not in ("checks",)}, "run_id": run_id, "created_at": now, "tickets": []})
    ledger.save()

    focus_ccys = newsmod.pair_currencies(selected) if selected else None
    report_macro, report_cot = macro, cot
    if selected and macro:
        report_macro = {**macro, "currencies": {k: v for k, v in macro.get("currencies", {}).items() if k in focus_ccys},
                        "pairs": {selected: macro.get("pairs", {}).get(selected, {"long": "neutral", "short": "neutral"})}}
    if selected and cot.get("available"):
        report_cot = {**cot, "by_ccy": {k: v for k, v in cot.get("by_ccy", {}).items() if k in focus_ccys}}
        if not report_cot["by_ccy"]:
            report_cot = {"available": False, "note": "ไม่มีข้อมูล COT ของสินทรัพย์ที่เลือก"}
    report = {
        "run_id": run_id, "slot": slot, "mode": eff_mode, "mode_requested": mode,
        "selected_symbol": selected,
        "strategy_version": cfg["version"], "generated_at": now, "generated_iso": iso(now),
        "report_tz": profile.get("report_timezone", "Asia/Bangkok"), "source": source.kind,
        "decision": decision, "gates": gates, "warnings": warnings, "limitations": limitations,
        "clock": clock.describe(),
        "account": {"masked": ("***" + acct.login[-3:]) if len(acct.login) > 3 else "***", "server": acct.server,
                    "currency": acct.currency, "balance": acct.balance, "equity": acct.equity, "free_margin": acct.free_margin,
                    "margin": acct.margin, "margin_level": acct.margin_level, "leverage": acct.leverage, "mode": acct.mode,
                    "trade_mode": acct.trade_mode},
        "risk": {"capital_base": min(acct.balance, acct.equity), "portfolio_cap": rp["portfolio_cap"], "currency_cap": rp["currency_cap"],
                 "reserved_before": rk.portfolio_exposure(positions, orders, symbols).total, "reserved_after": reserved_after,
                 "by_ccy": by_ccy, "unknown": ex.unknown, "orders_today": orders_today + len(chosen) if eff_mode != "plan_only" else orders_today,
                 "quota": cfg["frequency"]["max_new_orders_per_day"], "equity": eq, "dd_multiplier": ddm,
                 "daily_loss_stop": rp["daily_loss_stop"]},
        "management": management,
        "scan": scan_rows,
        "plans": chosen,
        "others": [{k: p[k] for k in ("plan_id", "symbol", "side", "setup", "status", "entry", "sl", "tp", "rr_net", "score")} |
                   {"fails": [c["name"] for c in p["checks"] if not c["ok"]]} for p in candidates if p["status"] != "PROPOSED"],
        "news": [{"t": e.t, "ccy": e.currency, "tier": e.tier, "name": e.name, "forecast": e.forecast, "previous": e.previous, "actual": e.actual}
                 for e in events if e.tier in (1, 2) and now - 3600 <= e.t <= now + 36 * 3600
                 and (focus_ccys is None or e.currency in focus_ccys)],
        "macro": report_macro, "cot": report_cot,
        "charts": {p["plan_id"]: chart_for(contexts[p["std_symbol"]], p, now, events) for p in chosen},
        "narrative": {}, "actions": [], "execution": None,
    }
    # เฝ้าดูรายคู่: ทุกคู่ที่ยังไม่มีแผน — บอกว่ารออะไร รอตรงไหน (เพื่อการเรียนรู้)
    watch = []
    chosen_std = {p["std_symbol"] for p in chosen}
    tz = report["report_tz"]
    for std, ctx in contexts.items():
        if std in chosen_std:
            continue
        cand = max([p for p in candidates if p["std_symbol"] == std], key=lambda p: p["score"]["total"], default=None)
        mp = ((macro or {}).get("pairs", {}) or {}).get(std)
        sym = symbols.get(ctx.broker_name)
        try:
            w = watch_for(ctx, now, cfg, events, mp, sym.digits if sym else 5, tz, cand)
        except Exception as e:  # noqa: BLE001 — ส่วนเรียนรู้ห้ามทำให้ทั้งรอบล้ม
            warnings.append(f"สร้างส่วนเฝ้าดู {std} ไม่ได้: {str(e)[:80]}")
            continue
        watch.append(w)
        report["charts"]["watch:" + std] = chart_watch(ctx, w, now, events)
    watch.sort(key=lambda w: (-w["progress"], not w["side_ok"], w["dist_atr"], w["symbol"]))
    wmap = {w["symbol"]: w for w in watch}
    for row in scan_rows:
        w = wmap.get(row["symbol"])
        if w and row.get("result") in ("เฝ้าดู", "ข้าม"):
            row["reason"] = f"{w['status']} ({w['progress']}/4)"
    report["watch"] = watch
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    if hasattr(source, "save_raw") and source.kind == "live":
        source.save_raw(run_dir / "raw")
    return run_dir, report


def compact_summary(report: dict, run_dir: Path) -> dict:
    """ผลสรุปขนาดเล็กสำหรับโมเดล (ไม่มีแท่งเทียน)"""
    tz = report["report_tz"]
    return {
        "run_id": report["run_id"], "mode": report["mode"], "decision": report["decision"],
        "selected_symbol": report.get("selected_symbol"),
        "gates": report["gates"], "warnings": report["warnings"], "limitations": report["limitations"],
        "account": {k: report["account"][k] for k in ("currency", "balance", "equity", "trade_mode", "mode")},
        "risk": {"reserved_before": round(report["risk"]["reserved_before"], 2), "reserved_after": round(report["risk"]["reserved_after"], 2),
                 "orders_today": report["risk"]["orders_today"], "quota": report["risk"]["quota"]},
        "plans": [{"plan_id": p["plan_id"], "symbol": p["symbol"], "side": p["side"], "type": p["order_type"], "setup": p["setup"],
                   "entry": p["entry"], "sl": p["sl"], "tp": p["tp"], "tp_name": p["tp_name"], "lot": p["volume"],
                   "risk": round(p["risk_money"], 2), "risk_pct": round(p["risk_pct"] * 100, 3), "rr_net": round(p["rr_net"], 2),
                   "score": p["score"]["total"], "expiry_th": fmt_local(p["expiry_utc"], tz), "macro": p["macro"],
                   "evidence": {"h4": p["signal"]["h4_bias"], "d1": p["signal"]["d1_state"],
                                "sweep": (p["signal"]["sweep"] or {}).get("level_name"), "zone": (p["signal"]["pullback"] or {}).get("zone"),
                                "fvg": p["signal"]["entry_from_fvg"], "disp_atr": p["signal"]["disp_move_atr"]}} for p in report["plans"]],
        "management": [{k: m.get(k) for k in ("ticket", "symbol", "kind", "side", "owner", "action", "reason", "new_sl")} for m in report["management"]],
        "scan": [{k: r.get(k) for k in ("symbol", "h4_bias", "result", "reason", "score")} for r in report["scan"]],
        "next_news": [{"time_th": fmt_local(n["t"], tz), "ccy": n["ccy"], "tier": n["tier"], "name": n["name"]} for n in report["news"][:8]],
        "watch": [{"symbol": w["symbol"], "side": w["side"], "status": w["status"], "progress": f"{w['progress']}/4",
                   "wait": [x["text"] for x in w["wait"][:2]]} for w in report.get("watch", [])],
        "narrative_file": str(run_dir / "narrative.json"),
        "report_html": str(run_dir / "report.html"),
    }
