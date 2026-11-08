"""เตรียมคำสั่งและกระทบยอด — ไฟล์นี้ไม่มีโค้ดส่งคำสั่งซื้อขาย
โมเดลเป็นผู้เรียกเครื่องมือ trade_* เองตามรายการที่ prepare_actions สร้าง แล้วส่งผลกลับด้วย record_result"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import adapters as ad
from . import news as newsmod
from . import risk as rk
from .datasource import _fill, load_map, server_now_from_time_info
from .engine import load_profile, load_strategy
from .ledger import Ledger
from .timeutil import ServerClock, fmt_local, iso, trading_day_key

UTC = timezone.utc


class ExecutionRefused(RuntimeError):
    pass


def _comment(prefix: str, plan: dict) -> str:
    t = datetime.fromtimestamp(plan["signal"]["bos_t"], UTC).strftime("%m%d%H%M")
    return f"{prefix} {plan['std_symbol']}{'L' if plan['side'] == 'long' else 'S'}{t}"[:31]


def _gate_account(profile: dict, acct: ad.Account):
    ex = profile.get("execution", {})
    if not ex.get("enabled"):
        raise ExecutionRefused("execution.enabled = false ใน account-profile.json — ยังไม่เปิดใช้การส่งคำสั่ง")
    if acct.trade_mode != "demo" and not ex.get("allow_live"):
        raise ExecutionRefused(f"บัญชีเป็น '{acct.trade_mode}' และ allow_live = false — อนุญาตเฉพาะ Demo")
    exp = profile.get("expected_account_login")
    if exp and str(exp) != acct.login:
        raise ExecutionRefused("บัญชีที่เชื่อมอยู่ไม่ตรงกับ Profile")


def prepare_actions(root: Path, source, run_id: str, plan_ids: list[str], authorization: str,
                    include_cancels: bool = False, now: float | None = None) -> dict:
    cfg, (profile, _) = load_strategy(), load_profile(root)
    mp = load_map()
    now = now or time.time()
    clock = ServerClock.from_profile(profile.get("server_time"), server_now_from_time_info(source.time_info()), datetime.fromtimestamp(now, UTC))
    acct = ad.parse_account(source.account())
    _gate_account(profile, acct)
    run_dir = root / "runs" / run_id
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    ledger = Ledger(root)
    day_key = trading_day_key(now, cfg["sessions"]["trading_day_rollover"])
    out = {"run_id": run_id, "authorization": authorization, "prepared_at": now, "actions": [], "refused": []}
    with ledger.lock("prepare-actions"):
        positions, orders = ad.parse_positions_orders(source.positions(), clock)
        symbols = ad.parse_symbols(source.symbols(), clock)
        ex = rk.portfolio_exposure(positions, orders, symbols)
        if ex.unknown:
            raise ExecutionRefused("มีไม้ที่คำนวณความเสี่ยงไม่ได้ — หยุดเปิดเพิ่ม")
        try:
            f_srv = clock.to_server(datetime.fromtimestamp(now - 86400, UTC))
            t_srv = clock.to_server(datetime.fromtimestamp(now + 2 * 86400, UTC))
            events = newsmod.classify(ad.parse_calendar(source.calendar(f_srv, t_srv), clock))
        except Exception:  # noqa: BLE001
            events = None
        quota = cfg["frequency"]["max_new_orders_per_day"] - ledger.orders_today(day_key)
        n = len([a for a in ledger.data["actions"] if a.get("run_id") == run_id])
        for pid in plan_ids:
            plan = ledger.get_plan(pid)
            if not plan:
                out["refused"].append({"plan_id": pid, "reason": "ไม่พบแผนใน Ledger"})
                continue
            reasons = []
            if plan.get("status") not in ("PROPOSED",):
                reasons.append(f"สถานะแผนคือ {plan.get('status')}")
            if plan["expiry_utc"] - now < 900:
                reasons.append("แผนหมดอายุหรือเหลือไม่ถึง 15 นาที")
            if quota <= 0:
                reasons.append("ครบโควตา 2 ออเดอร์ของวันนี้แล้ว")
            if events is None:
                reasons.append("อ่านปฏิทินข่าวไม่ได้")
            else:
                ccys = newsmod.pair_currencies(plan["std_symbol"])
                b = newsmod.blocked_now(now, events, ccys, cfg["news"])
                if b:
                    reasons.append(f"อยู่ในช่วงงดข่าว: {b.name}")
                nx = newsmod.next_tier1_start(now, events, ccys, cfg["news"])
                if nx and nx[0] < plan["expiry_utc"]:
                    reasons.append(f"ข่าว Tier 1 ({nx[1].name}) จะเริ่มก่อน Pending หมดอายุ — ต้องสแกนใหม่")
            sym = symbols.get(plan["symbol"])
            if sym is None or sym.missing:
                extra = source.symbol_info(plan["symbol"])
                if extra:
                    sym = ad.parse_symbols(extra, clock).get(plan["symbol"], sym)
            if sym is None:
                reasons.append("ไม่มีข้อมูล Symbol")
            else:
                if plan["symbol"] in ex.symbols:
                    reasons.append("มีไอเดียในคู่นี้อยู่แล้ว")
                if plan["side"] == "long" and not (plan["entry"] < sym.ask - sym.stops_level * sym.point):
                    reasons.append(f"ราคาเข้า {plan['entry']} ไม่ต่ำกว่า Ask {sym.ask} แล้ว — ห้ามเปลี่ยนเป็น Market")
                if plan["side"] == "short" and not (plan["entry"] > sym.bid + sym.stops_level * sym.point):
                    reasons.append(f"ราคาเข้า {plan['entry']} ไม่สูงกว่า Bid {sym.bid} แล้ว — ห้ามเปลี่ยนเป็น Market")
                try:
                    h1 = ad.parse_bars(source.bars(plan["symbol"], "H1", 60), clock)
                    after = [b for b in h1 if b.t >= plan["signal"]["bos_t"] + 3600]
                    if plan["side"] == "long" and any(b.l <= plan["entry"] for b in after):
                        reasons.append("ราคาย้อนมาแตะโซนเข้าแล้ว (โซนถูกใช้)")
                    if plan["side"] == "short" and any(b.h >= plan["entry"] for b in after):
                        reasons.append("ราคาย้อนมาแตะโซนเข้าแล้ว (โซนถูกใช้)")
                except Exception as e:  # noqa: BLE001
                    reasons.append(f"อ่านแท่งล่าสุดไม่ได้: {str(e)[:60]}")
            if not reasons and sym is not None:
                ddm = 1.0
                eq = ledger.data.get("hwm")
                if eq:
                    ddm, _ = rk.dd_multiplier((eq - acct.equity) / eq, profile)
                rs = rk.size_position(acct, sym, plan["side"], plan["entry"], plan["sl"], ex, profile, ddm, cfg)
                if not rs.ok:
                    reasons.append(f"คำนวณล็อตใหม่ไม่ผ่าน: {rs.reason}")
                else:
                    vol = min(rs.volume, plan["volume"])
            if reasons:
                out["refused"].append({"plan_id": pid, "reason": "; ".join(reasons)})
                continue
            n += 1
            prefix = profile["execution"].get("comment_prefix", "nsl")
            exp_server = clock.to_server(datetime.fromtimestamp(plan["expiry_utc"], UTC)).strftime(mp.get("time_format", "%Y-%m-%dT%H:%M:%S"))
            ot_map = mp.get("order_types", {})
            params = {"symbol": plan["symbol"], "order_type": ot_map.get(plan["order_type"], plan["order_type"]), "volume": vol,
                      "price": plan["entry"], "sl": plan["sl"], "tp": plan["tp"], "expiry_server": exp_server,
                      "expiry_utc": iso(plan["expiry_utc"]), "comment": _comment(prefix, plan), "magic": profile["execution"].get("magic")}
            tpl = mp["trade_templates"]["PLACE_PENDING"]
            act = {"action_id": f"{run_id}-A{n}", "run_id": run_id, "type": "PLACE_PENDING", "plan_id": plan["plan_id"],
                   "params": params, "tool": tpl["tool"], "args": _fill(tpl["args"], params), "status": "PREPARED",
                   "volume_note": "ลดล็อตตามงบปัจจุบัน" if vol < plan["volume"] else None,
                   "expiry_th": fmt_local(plan["expiry_utc"], profile.get("report_timezone", "Asia/Bangkok"))}
            out["actions"].append(act)
            ledger.add_action({**act, "authorization": authorization, "created_at": now})
            ex.total += vol * (rs.loss_per_lot + rs.cost_per_lot)
            ex.add_ccy(sym.base, sym.quote, plan["side"], vol * (rs.loss_per_lot + rs.cost_per_lot))
            ex.symbols.add(plan["symbol"])
            quota -= 1
        if include_cancels:
            for m in report.get("management", []):
                if m.get("action") == "CANCEL" and m.get("owner") == "skill" and m.get("kind") == "pending":
                    if not any(o.ticket == m["ticket"] for o in orders):
                        continue
                    n += 1
                    tpl = mp["trade_templates"]["CANCEL_PENDING"]
                    act = {"action_id": f"{run_id}-A{n}", "run_id": run_id, "type": "CANCEL_PENDING", "plan_id": m.get("plan_id"),
                           "params": {"ticket": m["ticket"], "symbol": m["symbol"]}, "tool": tpl["tool"],
                           "args": _fill(tpl["args"], {"ticket": int(m["ticket"]) if str(m["ticket"]).isdigit() else m["ticket"], "symbol": m["symbol"]}), "status": "PREPARED", "reason": m["reason"]}
                    out["actions"].insert(0, act)  # ยกเลิกก่อนสร้างใหม่
                    ledger.add_action({**act, "authorization": authorization, "created_at": now})
        ledger.save()
    (run_dir / "actions.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


def _parse_trade_response(resp) -> dict:
    d = resp
    if isinstance(d, str):
        try:
            d = json.loads(d)
        except json.JSONDecodeError:
            return {"status": "unknown", "note": d[:200]}
    if isinstance(d, dict) and len(d) == 1 and isinstance(next(iter(d.values())), dict):
        d = next(iter(d.values()))
    if not isinstance(d, dict):
        return {"status": "unknown", "note": str(d)[:200]}
    k = {str(a).lower(): b for a, b in d.items()}
    rc = k.get("retcode", k.get("result_code", k.get("code")))
    ticket = k.get("order") or k.get("ticket") or k.get("position") or k.get("deal")
    ok_flag = k.get("success", k.get("ok"))
    rc_s = str(rc)
    if rc_s in ("10009", "10008", "TRADE_RETCODE_DONE", "TRADE_RETCODE_PLACED") or ok_flag is True:
        st = "submitted"
    elif rc is not None or ok_flag is False:
        st = "rejected"
    else:
        st = "unknown"
    return {"status": st, "retcode": rc, "ticket": str(ticket) if ticket else None, "comment": k.get("comment") or k.get("message")}


def record_result(root: Path, run_id: str, action_id: str, response) -> dict:
    cfg = load_strategy()
    ledger = Ledger(root)
    parsed = _parse_trade_response(response)
    run_dir = root / "runs" / run_id
    raw_dir = run_dir / "responses"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{action_id}.json").write_text(json.dumps(response, ensure_ascii=False, default=str), encoding="utf-8")
    act = next((a for a in ledger.data["actions"] if a.get("action_id") == action_id), None)
    if not act:
        raise ValueError(f"ไม่พบ action {action_id}")
    act.update(status=parsed["status"].upper(), retcode=parsed.get("retcode"), ticket=parsed.get("ticket"), responded_at=time.time())
    if act["type"] == "PLACE_PENDING" and parsed["status"] in ("submitted", "unknown"):
        plan = ledger.get_plan(act["plan_id"])
        if plan:
            plan["status"] = "SUBMITTED" if parsed["status"] == "submitted" else "SUBMISSION_UNKNOWN"
            if parsed.get("ticket"):
                plan.setdefault("tickets", []).append(parsed["ticket"])
            plan["comment"] = act["params"]["comment"]
        ledger.note_order(trading_day_key(time.time(), cfg["sessions"]["trading_day_rollover"]), act["plan_id"])
    ledger.save()
    return {"action_id": action_id, **parsed}


def reconcile(root: Path, source, run_id: str) -> dict:
    """อ่านสถานะจริงจาก MT5 แล้วจับคู่กับ Action ของรอบนี้"""
    _, (profile, _) = load_strategy(), load_profile(root)
    clock = ServerClock.from_profile(profile.get("server_time"), server_now_from_time_info(source.time_info()))
    ledger = Ledger(root)
    positions, orders = ad.parse_positions_orders(source.positions(), clock)
    acct = ad.parse_account(source.account())
    symbols = ad.parse_symbols(source.symbols(), clock)
    res = []
    for a in [x for x in ledger.data["actions"] if x.get("run_id") == run_id]:
        row = {"action_id": a["action_id"], "type": a["type"], "symbol": a["params"].get("symbol"), "ticket": a.get("ticket")}
        if a["type"] == "PLACE_PENDING":
            c = a["params"]["comment"]
            o = next((o for o in orders if o.ticket == a.get("ticket") or (o.comment and c.startswith(o.comment[:20]) and o.symbol == a["params"]["symbol"])), None)
            p = next((p for p in positions if p.comment and c.startswith(p.comment[:20]) and p.symbol == a["params"]["symbol"]), None)
            if o:
                row.update(status="confirmed", ticket=o.ticket, accepted={"type": o.order_type, "lot": o.volume, "price": o.price, "sl": o.sl, "tp": o.tp, "expiry": fmt_local(o.expiry_utc)})
                a["status"], a["ticket"] = "CONFIRMED_PENDING", o.ticket
            elif p:
                row.update(status="confirmed", ticket=p.ticket, accepted={"filled": True, "lot": p.volume, "price": p.price_open, "sl": p.sl, "tp": p.tp})
                a["status"], a["ticket"] = "FILLED", p.ticket
            elif a.get("status") == "REJECTED":
                row.update(status="rejected", note=f"retcode {a.get('retcode')}")
            elif a.get("status") == "PREPARED":
                row.update(status="not_sent", note="ยังไม่ได้ส่ง")
            else:
                row.update(status="unknown", note="ไม่พบออเดอร์ในบัญชี — ห้ามส่งซ้ำจนกว่าจะตรวจประวัติ")
            plan = ledger.get_plan(a["plan_id"])
            if plan and row.get("ticket"):
                if row["ticket"] not in plan.setdefault("tickets", []):
                    plan["tickets"].append(row["ticket"])
                plan["status"] = "PENDING" if a["status"] == "CONFIRMED_PENDING" else "FILLED" if a["status"] == "FILLED" else plan["status"]
        elif a["type"] == "CANCEL_PENDING":
            gone = not any(o.ticket == a["params"]["ticket"] for o in orders)
            filled = any(p.ticket == a["params"]["ticket"] for p in positions)
            row.update(status="confirmed" if gone and not filled else "filled_before_cancel" if filled else "unknown",
                       ticket=a["params"]["ticket"])
            a["status"] = "CONFIRMED" if gone else "UNKNOWN"
        res.append(row)
    ledger.save()
    statuses = {r["status"] for r in res if r["status"] != "not_sent"}
    overall = "success" if statuses == {"confirmed"} else "rejected" if statuses == {"rejected"} else "unknown" if "unknown" in statuses or not statuses else "partial"
    ex = rk.portfolio_exposure(positions, orders, symbols)
    execution = {"status": overall, "verified_at": time.time(), "actions": res,
                 "after": {"equity": acct.equity, "reserved": ex.total, "positions": len(positions), "orders": len(orders)}}
    run_dir = root / "runs" / run_id
    rp = run_dir / "report.json"
    report = json.loads(rp.read_text(encoding="utf-8"))
    report["execution"] = execution
    for p in report.get("plans", []):
        lp = ledger.get_plan(p["plan_id"])
        if lp:
            p["status"] = lp.get("status", p["status"])
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return execution
