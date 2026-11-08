"""คะแนนคุณภาพ (ข้อ 8.2) — ใช้เรียงลำดับเท่านั้น ไม่ใช่โอกาสชนะ และห้ามใช้เพิ่มล็อต
แต่ละกลุ่มให้ 0 / 50 / 100% ของคะแนนเต็ม"""
from __future__ import annotations

from .timeutil import window_bounds_utc, local_date


def _lvl(x: float) -> float:
    return 1.0 if x >= 1.0 else 0.5 if x >= 0.5 else 0.0


def score(sig, rr_net: float | None, cost_r: float | None, macro: str, cfg: dict) -> dict:
    w = cfg["score"]["weights"]
    parts, why = {}, {}

    b = 1.0 if sig.d1_state == "support" else 0.5
    parts["bias"] = w["bias"] * b
    why["bias"] = "D1 สนับสนุน" if b == 1 else "D1 เป็นกลาง"

    q = 0.0
    q += 0.5 if sig.entry_from_fvg else 0.0
    q += 0.5 if (sig.setup == "A+B" or sig.disp_move_atr >= 2.0) else 0.0
    if sig.tier2_in_signal:
        q -= 0.5
    q = _lvl(max(0.0, q))
    parts["setup"] = w["setup"] * q
    why["setup"] = ("มี FVG จริง" if sig.entry_from_fvg else "ไม่มี FVG ใช้ 50% ของแท่งแรง") + \
        (", เข้าเงื่อนไข A+B" if sig.setup == "A+B" else f", แท่งแรง {sig.disp_move_atr:.1f}×ATR") + \
        (", มีข่าว Tier 2 ในแท่งสัญญาณ" if sig.tier2_in_signal else "")

    tc = 0.0
    if rr_net is not None:
        tc = 1.0 if (rr_net >= 2.0 and (cost_r or 0) <= 0.08) else 0.5 if rr_net >= cfg["min_rr_net"] else 0.0
    parts["target_cost"] = w["target_cost"] * tc
    why["target_cost"] = f"R:R สุทธิ {rr_net:.2f}" if rr_net is not None else "คำนวณ R:R ไม่ได้"

    ms = 0.0
    ms += 0.5 if macro == "support" else 0.0
    win = cfg["sessions"][sig.window]
    a, _ = window_bounds_utc(local_date(sig.bos_close_t, win["tz"]), win)
    early = sig.bos_close_t - a <= 2 * 3600
    ms += 0.5 if early else 0.0
    parts["macro_session"] = w["macro_session"] * ms
    why["macro_session"] = f"Macro {macro}" + (", สัญญาณช่วงต้นของ Session" if early else "")

    total = round(sum(parts.values()), 1)
    return {"total": total, "parts": parts, "why": why, "threshold": cfg["score"]["threshold"],
            "pass": total >= cfg["score"]["threshold"]}
