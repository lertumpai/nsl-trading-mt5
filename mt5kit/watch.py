"""เฝ้าดูรายคู่: ตอนยังไม่มีจุดเข้า ให้บอกว่ากำลังรออะไร รอตรงไหน และไอเดียผิดเมื่อไร (เพื่อการเรียนรู้)

ทุกตัวเลขมาจากโครงสร้างราคาจริงด้วยโค้ด ข้อความภาษาไทยสร้างจากแม่แบบ โมเดลไม่ได้แต่งตัวเลข
"""
from __future__ import annotations

from . import news as newsmod
from . import structure as st
from .setups import H1, H4, Context, liquidity_levels
from .timeutil import fmt_local, next_local_time_utc


def _f(v, d):
    return None if v is None else f"{v:.{d}f}"


def _next_session_text(now: float, cfg: dict, tz: str) -> str:
    """เวลาเริ่มของหน้าต่างลอนดอน/นิวยอร์กที่ยังไม่จบ (เวลาไทย)"""
    s = cfg["sessions"]
    lon = next_local_time_utc(now - 4 * 3600, s["london"]["tz"], s["london"]["start"])
    ny = next_local_time_utc(now - 4 * 3600, s["newyork"]["tz"], s["newyork"]["start"])
    items = sorted([(lon, "ลอนดอน"), (ny, "นิวยอร์ก")])
    return " · ".join(f"{n} {fmt_local(t, tz)} น." for t, n in items)


def watch_for(ctx: Context, now: float, cfg: dict, events: list, macro_pair: dict | None,
              digits: int, tz: str = "Asia/Bangkok", candidate: dict | None = None) -> dict:
    h1, h4 = ctx.h1, ctx.h4
    last = h1[-1]
    t_close = last.t + H1
    h4i, d1i = ctx.h4_idx(t_close), ctx.d1_idx(t_close)
    h4b = st.bias(ctx.h4_sw, h4i)
    d1l = st.d1_state(ctx.d1, ctx.d1_sw, ctx.d1_ema, d1i, "long")
    d1s = st.d1_state(ctx.d1, ctx.d1_sw, ctx.d1_ema, d1i, "short")
    atr_h1 = st.atr_wilder(h1, cfg["atr_period"])[-1] or 0.0
    atr_h4 = st.atr_wilder(h4, cfg["atr_period"])[-1] or atr_h1 * 2
    px = last.c
    F = lambda v: _f(v, digits)  # noqa: E731

    side = "long" if h4b == "up" else "short" if h4b == "down" else None
    d1_for = {"long": d1l, "short": d1s}
    if side and d1_for[side] == "opposite":
        side_ok, why_side = False, f"H4 {'ขาขึ้น' if side == 'long' else 'ขาลง'} แต่ D1 สวนทาง (โครงสร้างและ EMA50 อยู่อีกฝั่ง) — กฎห้ามเทรด"
    elif side:
        side_ok, why_side = True, f"H4 {'ขาขึ้น' if side == 'long' else 'ขาลง'} · D1 {'สนับสนุน' if d1_for[side] == 'support' else 'เป็นกลาง'}"
    else:
        d1_side = "long" if d1l == "support" else "short" if d1s == "support" else None
        side = d1_side
        side_ok = d1_side is not None
        why_side = ("H4 ยังไม่มีทิศทาง แต่ D1 " + ("ขาขึ้น" if d1_side == "long" else "ขาลง") + " → รอได้เฉพาะรูปแบบ B") if d1_side else \
            "H4 ยังไม่มีทิศทาง และ D1 ไม่ชัด → ยังไม่มีอะไรให้รอ"
    out = {
        "symbol": ctx.symbol, "broker": getattr(ctx, "broker_name", ctx.symbol), "digits": digits, "price": px,
        "price_t": last.t, "h4_bias": h4b, "d1_long": d1l, "d1_short": d1s, "side": side, "side_ok": side_ok,
        "atr_h1": atr_h1, "zones": [], "levels": [], "steps": [], "wait": [], "scenario": [], "invalidation": None,
        "trigger": None, "target": None, "progress": 0, "candidate": None, "dist_atr": 99.0,
    }
    nxt = newsmod.next_tier1_start(now, events, ctx.ccys, cfg["news"])
    if nxt:
        out["news_note"] = f"ข่าว Tier 1 ถัดไป: {nxt[1].currency} {nxt[1].name} {fmt_local(nxt[1].t, tz)} น. (งด Pending ตั้งแต่ {fmt_local(nxt[0], tz)} น.)"
    sw_h = st.confirmed(ctx.h4_sw, h4i, "high")[-2:]
    sw_l = st.confirmed(ctx.h4_sw, h4i, "low")[-2:]
    if h4b == "neutral":
        for s in sw_h:
            out["levels"].append({"name": "Swing High H4", "price": s.price, "kind": "structure"})
        for s in sw_l:
            out["levels"].append({"name": "Swing Low H4", "price": s.price, "kind": "structure"})
        if len(sw_h) == 2 and len(sw_l) == 2:
            out["wait"].append({"label": "โครงสร้าง H4", "text":
                f"H4 ยังไม่มีทิศทาง: Swing High ล่าสุด {F(sw_h[0].price)} → {F(sw_h[1].price)}, Swing Low {F(sw_l[0].price)} → {F(sw_l[1].price)} "
                "ขาขึ้นต้องได้ทั้ง High และ Low ที่สูงขึ้น ขาลงต้องได้ทั้งคู่ที่ต่ำลง"})
    out["steps"].append({"n": 1, "label": "ทิศทาง H4/D1", "ok": side_ok, "detail": why_side})
    if not side_ok or side is None:
        out["steps"] += [{"n": 2, "label": "ราคาเข้าโซน / กวาดสภาพคล่อง", "ok": False, "detail": "ยังไม่เริ่ม"},
                         {"n": 3, "label": "แท่งแรง + ปิดทะลุโครงสร้าง (BOS)", "ok": False, "detail": "ยังไม่เริ่ม"},
                         {"n": 4, "label": "FVG + R:R ≥ 1.5 → วาง Pending", "ok": False, "detail": "ยังไม่เริ่ม"}]
        if not out["wait"]:
            out["wait"].append({"label": "ทิศทาง", "text": why_side})
        elif side is not None:
            out["wait"].append({"label": "ทิศทาง", "text": why_side})
        out["status"] = "ยังไม่มีทิศทางให้รอ" if not side_ok else "รอ"
        return out

    up = side == "long"
    allow_a = h4b in ("up", "down")
    zone_hit, zone_text, leg_broken = False, None, False
    trigger = None
    # ---------- รูปแบบ A: โซนย่อของขา H4 ----------
    if allow_a:
        leg = st.h4_leg(h4, ctx.h4_sw, h4i, side)
        if leg:
            origin, oidx, ext, eidx = leg
            # ใช้ H1 หาจุดสุดขาล่าสุด (รวมแท่ง H1 หลังแท่ง H4 ที่ปิดล่าสุดด้วย)
            o1 = ctx.h1_index_from(h4[oidx].t)
            seg_all = h1[o1:]
            if seg_all:
                k = max(range(len(seg_all)), key=lambda q: seg_all[q].h) if up else min(range(len(seg_all)), key=lambda q: seg_all[q].l)
                idx_ext = o1 + k
                ext = h1[idx_ext].h if up else h1[idx_ext].l
            else:
                idx_ext = len(h1) - 1
            half = origin + 0.5 * (ext - origin)
            seg = h1[idx_ext + 1:]
            pb_ext = (min(b.l for b in seg) if up else max(b.h for b in seg)) if seg else px
            broken = (pb_ext <= origin) if up else (pb_ext >= origin)
            top, bot = (half, origin) if up else (origin, half)
            out["zones"].append({"kind": "zone", "label": "โซนย่อ (Discount)" if up else "โซนดีด (Premium)",
                                 "top": top, "bottom": bot, "t1": h1[idx_ext].t, "t2": None})
            fvgs = []
            want = "bull" if up else "bear"
            for f in ctx.h4_fvg:
                if f.direction == want and oidx < f.idx3 <= eidx and st.untouched_after(h4, f.idx3, f.top if up else f.bottom, side, eidx):
                    fvgs.append(f)
            for f in fvgs[-2:]:
                out["zones"].append({"kind": "fvg", "label": "FVG H4", "top": f.top, "bottom": f.bottom, "t1": f.t1, "t2": None})
            out["invalidation"] = {"price": origin, "text": f"ถ้าราคาหลุด {F(origin)} (ต้นขา H4) ไอเดียรูปแบบ A จบ ต้องรอโครงสร้างใหม่"}
            out["levels"].append({"name": "จุดไอเดียผิด (ต้นขา H4)", "price": origin, "kind": "invalid"})
            out["target"] = {"price": ext, "text": f"เป้าหมายแรกคือยอดขา H4 {F(ext)}" if up else f"เป้าหมายแรกคือก้นขา H4 {F(ext)}"}
            out["levels"].append({"name": "เป้าหมาย (สุดขา H4)", "price": ext, "kind": "target"})
            if broken:
                leg_broken = True
                zone_text = f"ราคาหลุดต้นขา H4 ({F(origin)}) แล้ว — ขานี้ใช้ไม่ได้ รอ H4 สร้าง Swing ใหม่"
            else:
                reached = (pb_ext <= half) if up else (pb_ext >= half)
                for f in fvgs:
                    if (pb_ext <= f.top) if up else (pb_ext >= f.bottom):
                        reached = True
                zone_hit = reached
                dist = abs(px - half) / atr_h1 if atr_h1 else 0
                out["dist_atr"] = 0.0 if reached else round(dist, 2)
                if reached:
                    zone_text = f"ราคาย่อถึงโซนแล้ว (ต่ำสุดของการย่อ {F(pb_ext)})" if up else f"ราคาดีดถึงโซนแล้ว (สูงสุดของการดีด {F(pb_ext)})"
                else:
                    zone_text = (f"รอราคาย่อลงต่ำกว่า {F(half)} (ครึ่งล่างของขา H4 {F(origin)}–{F(ext)}) อีกประมาณ {dist:.1f}×ATR(H1)"
                                 if up else f"รอราคาดีดขึ้นเหนือ {F(half)} (ครึ่งบนของขา H4 {F(ext)}–{F(origin)}) อีกประมาณ {dist:.1f}×ATR(H1)")
                    if fvgs:
                        f = fvgs[-1]
                        zone_text += f" หรือแตะ FVG H4 {F(f.bottom)}–{F(f.top)}"
                # ระดับที่ต้องปิดทะลุ: Swing H1 ล่าสุดที่เกิดระหว่างย่อ (หลังจุดสุดขา)
                kind = "high" if up else "low"
                c = [w for w in st.confirmed(ctx.h1_sw, len(h1) - 1, kind) if w.idx > idx_ext]
                if c:
                    trigger = c[-1].price
                if not seg:
                    zone_text += " — ตอนนี้ราคาเพิ่งทำจุดสุดขาใหม่ ยังไม่เริ่มย่อ"
    # ---------- รูปแบบ B: ระดับสภาพคล่องที่รอให้ถูกกวาด ----------
    lv = liquidity_levels(ctx, now)
    names = ("ASIA_L", "PDL", "PWL") if up else ("ASIA_H", "PDH", "PWH")
    label = {"ASIA_L": "Low กรอบเอเชีย", "ASIA_H": "High กรอบเอเชีย", "PDL": "Low เมื่อวาน (PDL)", "PDH": "High เมื่อวาน (PDH)",
             "PWL": "Low สัปดาห์ก่อน (PWL)", "PWH": "High สัปดาห์ก่อน (PWH)"}
    sweep_targets = []
    for nm in names:
        if nm in lv:
            L = lv[nm][0]
            beyond = (px - L) if up else (L - px)
            if 0 < beyond <= 4 * atr_h4:
                sweep_targets.append((nm, L))
                out["levels"].append({"name": f"รอกวาด: {label[nm]}", "price": L, "kind": "sweep"})
    if "ASIA_H" in lv:
        out["zones"].append({"kind": "asia", "label": "กรอบเอเชีย", "top": lv["ASIA_H"][0], "bottom": lv["ASIA_L"][0],
                             "t1": lv["ASIA_H"][1] - 7 * 3600, "t2": lv["ASIA_H"][1]})
    sweep_text = None
    if sweep_targets:
        s_txt = ", ".join(f"{label[n]} {F(v)}" for n, v in sweep_targets)
        sweep_text = f"รอราคาทะลุลง {s_txt} แล้วปิดกลับขึ้นมาภายใน 2 แท่ง H1 ในช่วง {_next_session_text(now, cfg, tz)}" if up else \
            f"รอราคาทะลุขึ้น {s_txt} แล้วปิดกลับลงมาภายใน 2 แท่ง H1 ในช่วง {_next_session_text(now, cfg, tz)}"
    # ---------- ขั้นตอน ----------
    out["steps"].append({"n": 2, "label": "ราคาเข้าโซน / กวาดสภาพคล่อง", "ok": zone_hit,
                         "detail": zone_text or ("รูปแบบ A ใช้ไม่ได้ (H4 เป็นกลาง) รอเฉพาะการกวาดสภาพคล่อง" if not allow_a else "ยังไม่มีขา H4 ที่ชัด")})
    if trigger is not None:
        out["trigger"] = {"price": trigger, "text": f"รอแท่ง H1 แรง (รวมกัน ≥ {cfg['displacement']['min_move_atr']}×ATR) ปิด{'เหนือ' if up else 'ใต้'} {F(trigger)} ในช่วงลอนดอน/นิวยอร์ก"}
        out["levels"].append({"name": "รอปิดทะลุตรงนี้ (BOS)", "price": trigger, "kind": "trigger"})
    trig_txt = out["trigger"]["text"] if out["trigger"] else \
        f"เมื่อราคาเข้าโซนแล้ว ต้องรอให้เกิด Swing {'High' if up else 'Low'} H1 ระหว่างย่อก่อน แล้วรอแท่งแรงปิดทะลุ Swing นั้น"
    out["steps"].append({"n": 3, "label": "แท่งแรง + ปิดทะลุโครงสร้าง (BOS)", "ok": False, "detail": trig_txt})
    out["steps"].append({"n": 4, "label": "FVG + R:R ≥ 1.5 → วาง Pending", "ok": False,
                         "detail": "หลัง BOS จะวาง Limit ที่กึ่งกลาง FVG H1 SL หลังจุดต่ำสุด/สูงสุดของการย่อ TP ที่ระดับใกล้สุด"})
    if candidate:
        out["candidate"] = {"plan_id": candidate["plan_id"], "setup": candidate["setup"], "entry": candidate["entry"],
                            "sl": candidate["sl"], "tp": candidate["tp"], "rr_net": candidate["rr_net"],
                            "fails": [c["name"] for c in candidate["checks"] if not c["ok"]]}
        out["steps"][2]["ok"] = True
        out["steps"][2]["detail"] = f"เกิดสัญญาณรูปแบบ {candidate['setup']} แล้ว"
        out["steps"][3]["detail"] = "ไม่ผ่าน: " + "; ".join(out["candidate"]["fails"][:3])
    out["wait"] = []
    if zone_text:
        out["wait"].append({"label": "รูปแบบ A · ย่อตามเทรนด์", "text": zone_text})
    if sweep_text:
        out["wait"].append({"label": "รูปแบบ B · กวาดสภาพคล่อง", "text": sweep_text})
    out["wait"].append({"label": "ขั้นถัดไป", "text": trig_txt})
    if out["invalidation"]:
        out["wait"].append({"label": "ไอเดียผิดเมื่อ", "text": out["invalidation"]["text"]})
    macro = (macro_pair or {}).get(side, "neutral")
    out["macro"] = macro
    if macro == "conflict":
        out["wait"].insert(0, {"label": "Macro", "text": "Macro วันนี้ขัดแย้งกับทิศนี้ชัดเจน — แม้กราฟครบก็ยังไม่เทรด"})
    out["progress"] = sum(1 for s in out["steps"] if s["ok"])
    out["status"] = {0: "รอทิศทาง", 1: "รอราคาเข้าโซน", 2: "รอสัญญาณ BOS", 3: "มีสัญญาณแต่ไม่ผ่านกฎ", 4: "พร้อม"}[out["progress"]]
    if out["progress"] == 1 and (leg_broken or not allow_a):
        out["status"] = "รอกวาดสภาพคล่อง / โครงสร้างใหม่"
    # ---------- ภาพที่รอ (ฉากทัศน์ ไม่ใช่การทำนาย) ----------
    t0 = last.t + H1
    pts = [[t0, px]]
    zone_px = None
    if out["zones"] and out["zones"][0]["kind"] == "zone" and not zone_hit:
        z = out["zones"][0]
        zone_px = z["top"] - 0.35 * (z["top"] - z["bottom"]) if up else z["bottom"] + 0.35 * (z["top"] - z["bottom"])
    elif sweep_targets:
        zone_px = sweep_targets[0][1] - (0.2 * atr_h1 if up else -0.2 * atr_h1)
    if zone_px is not None:
        pts.append([t0 + 5 * 3600, zone_px])
    trig_px = trigger if trigger is not None else (pts[-1][1] + (1.5 * atr_h1 if up else -1.5 * atr_h1))
    pts.append([pts[-1][0] + 4 * 3600, trig_px + (0.3 * atr_h1 if up else -0.3 * atr_h1)])
    if out["target"] and ((out["target"]["price"] > trig_px) if up else (out["target"]["price"] < trig_px)):
        pts.append([pts[-1][0] + 10 * 3600, out["target"]["price"]])
    if len(pts) >= 2:
        out["scenario"] = [{"kind": "watch", "label": "ภาพที่รอ (ยังไม่เกิด)", "pts": pts}]
    return out


def chart_watch(ctx: Context, w: dict, now: float, events: list) -> dict:
    def candles(bars, n):
        return [[int(b.t), b.o, b.h, b.l, b.c] for b in bars[-n:]]
    boxes = []
    for z in w["zones"]:
        boxes.append({"t1": z["t1"], "t2": z["t2"] or (now + 30 * 3600), "top": z["top"], "bottom": z["bottom"],
                      "kind": z["kind"], "label": z["label"]})
    news = [{"t": e.t, "tier": e.tier, "ccy": e.currency, "name": e.name} for e in newsmod.events_for(events, ctx.ccys)
            if e.tier in (1, 2) and now - 86400 <= e.t <= now + 2 * 86400]
    return {"tfs": {"H1": candles(ctx.h1, 150), "H4": candles(ctx.h4, 150), "D1": candles(ctx.d1, 150)},
            "levels": w["levels"], "boxes": boxes, "scenario": w["scenario"], "news": news, "markers": [], "vlines": []}
