"""คำสั่งของ mt5kit: python3 -m mt5kit <command>

status            ตรวจการตั้งค่า (Profile, mcp-map, Lock, Ledger)
probe             เรียก tools/list + เครื่องมืออ่านข้อมูลที่ map ไว้ แล้วบันทึกผลดิบ (ใช้ครั้งแรก/เมื่อ MCP เปลี่ยน)
record --out DIR  บันทึกข้อมูลดิบทั้งชุดเป็น fixture สำหรับทดสอบตอนตลาดปิด
macro-template    สร้างไฟล์ Macro ของวันนี้ให้โมเดลกรอก
cot               ดึง COT (cache 3 วัน)
import-snapshot F แตกไฟล์ snapshot รวม (ดึงผ่านเบราว์เซอร์) เป็น fixture ใน runs/live-*
scan              วิเคราะห์สินทรัพย์ตามกลยุทธ์ → runs/<id>/report.json + report.html + สรุปสั้น (JSON)
analyze SYMBOL    วิเคราะห์รายตัวพร้อม report.html (แผนอย่างเดียว เช่น analyze XAUUSD)
render --run ID   รวม narrative.json แล้วสร้าง report.html ใหม่
prepare-actions   ตรวจซ้ำและสร้างรายการคำสั่งให้โมเดลเรียกเครื่องมือ MCP เอง
record-result     บันทึกผลตอบกลับของเครื่องมือ trade_* ทีละ Action
reconcile         อ่านสถานะจริงจาก MT5 แล้วกระทบยอด + อัปเดตรายงาน
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .mcp_client import find_project_root

UTC = timezone.utc


def _print(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False, indent=1, default=str) + "\n")


def _source(args):
    if getattr(args, "fixtures", None):
        from .datasource import FixtureSource
        return FixtureSource(Path(args.fixtures))
    from .datasource import LiveSource
    return LiveSource()


def _now(args):
    if getattr(args, "now", None):
        return datetime.fromisoformat(args.now.replace("Z", "+00:00")).astimezone(UTC).timestamp()
    return None


def _auto_slot(now_ts: float) -> str:
    from .engine import load_strategy
    from .timeutil import in_window
    s = load_strategy()["sessions"]
    if in_window(now_ts, {**s["london"], "start": "06:30", "end": "11:30"}):
        return "london"
    if in_window(now_ts, {**s["newyork"], "start": "07:30", "end": "12:30"}):
        return "newyork"
    return "manage"


def cmd_status(args, root):
    from .datasource import load_map
    from .ledger import Ledger
    mp = load_map()
    led = Ledger(root)
    lock = (root / "ledger" / ".lock").exists()
    _print({
        "project_root": str(root),
        "mcp_json": (root / ".mcp.json").exists(),
        "mcp_map_verified": mp.get("verified", False),
        "account_profile": (root / "account-profile.json").exists(),
        "execution_enabled": json.loads((root / "account-profile.json").read_text()).get("execution", {}).get("enabled") if (root / "account-profile.json").exists() else False,
        "lock_held": lock,
        "plans_in_ledger": len(led.data["plans"]),
        "orders_by_day": {k: len(v) for k, v in list(led.data["orders_by_day"].items())[-5:]},
        "macro_today": sorted(p.name for p in (root / "runs" / "macro").glob("*.json"))[-1:] if (root / "runs" / "macro").exists() else [],
        "next_steps": [s for s in [
            None if (root / ".mcp.json").exists() else "สร้าง .mcp.json จาก .mcp.example.json (ใส่ Token ใหม่)",
            None if mp.get("verified") else "รัน `python3 -m mt5kit probe` แล้วแก้ mt5kit/config/mcp-map.json ให้ตรง schema แล้วตั้ง verified=true",
            None if (root / "account-profile.json").exists() else "คัดลอก mt5kit/config/account-profile.example.json เป็น account-profile.json แล้วตรวจค่า",
        ] if s],
    })
    return 0


def cmd_probe(args, root):
    from .datasource import LiveSource, load_map
    from . import adapters as ad
    from .timeutil import ServerClock
    out_dir = root / "runs" / "probe"
    out_dir.mkdir(parents=True, exist_ok=True)
    src = LiveSource()
    tools = src.client.list_tools()
    (out_dir / "tools.json").write_text(json.dumps(tools, ensure_ascii=False, indent=1), encoding="utf-8")
    names = {t["name"] for t in tools}
    mp = load_map()
    wanted = {k: v["tool"] for k, v in mp["read"].items()}
    trade = {k: v["tool"] for k, v in mp["trade_templates"].items() if isinstance(v, dict)}
    schemas = {t["name"]: t.get("inputSchema", {}) for t in tools if t["name"] in set(wanted.values()) | set(trade.values()) or t["name"].startswith("economic_calendar")}
    (out_dir / "schemas.json").write_text(json.dumps(schemas, ensure_ascii=False, indent=1), encoding="utf-8")
    results = {}
    sym = args.symbol
    clock = ServerClock("ny_close")
    tests = [("time", {}), ("account", {}), ("positions", {}), ("symbols", {}), ("symbol_info", {"symbol": sym}),
             ("history", {"symbol": sym, "tf": mp["timeframes"]["H1"], "count": 50})]
    for logical, vals in tests:
        try:
            # History needs a datetime range; use the same path as the live scan.
            data = src.bars(sym, "H1", 50) if logical == "history" else src.call(logical, **vals)
            (out_dir / f"raw_{logical}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1)[:200000], encoding="utf-8")
            parsed = "ok"
            try:
                if logical == "account":
                    a = ad.parse_account(data); parsed = f"ok: {a.currency} equity {a.equity} mode {a.mode}/{a.trade_mode}"
                elif logical == "history":
                    parsed = f"ok: {len(ad.parse_bars(data, clock))} แท่ง"
                elif logical in ("symbols", "symbol_info"):
                    s = ad.parse_symbols(data, clock); one = s.get(sym) or next(iter(s.values()), None)
                    parsed = f"ok: {len(s)} symbols; {sym} missing={one.missing if one else 'ไม่พบ'}"
                elif logical == "positions":
                    p, o = ad.parse_positions_orders(data, clock); parsed = f"ok: {len(p)} positions, {len(o)} orders"
            except Exception as e:  # noqa: BLE001
                parsed = f"เรียกได้ แต่ adapter อ่านไม่ได้: {e}"
            results[logical] = parsed
        except Exception as e:  # noqa: BLE001
            results[logical] = f"error: {str(e)[:200]}"
    _print({"tools_count": len(tools),
            "missing_tools": sorted(t for t in set(wanted.values()) | set(trade.values()) if t not in names),
            "calendar_tools": sorted(n for n in names if "calendar" in n),
            "results": results, "saved": str(out_dir),
            "todo": "เปิด runs/probe/schemas.json เทียบชื่ออาร์กิวเมนต์กับ mt5kit/config/mcp-map.json แก้ให้ตรง แล้วรัน probe ซ้ำจนทุกบรรทัดเป็น ok จากนั้นตั้ง verified=true"})
    return 0


def cmd_record(args, root):
    from .datasource import LiveSource
    from .engine import load_strategy, load_profile, resolve_symbols
    from . import adapters as ad
    from .timeutil import ServerClock
    cfg = load_strategy()
    profile, _ = load_profile(root)
    src = LiveSource()
    src.time_info(); src.account(); src.positions()
    syms = ad.parse_symbols(src.symbols(), ServerClock())
    res = resolve_symbols(cfg["symbols"], list(syms), profile, cfg.get("symbol_aliases"))
    for std, r in res.items():
        if not r["ok"]:
            continue
        src.symbol_info(r["name"])
        for tf in ("W1", "D1", "H4", "H1"):
            src.bars(r["name"], tf, cfg["bars"][tf])
    now = datetime.now(UTC)
    from datetime import timedelta
    try:
        src.calendar(now - timedelta(days=2), now + timedelta(days=3))
    except Exception as e:  # noqa: BLE001
        print(f"calendar: {e}", file=sys.stderr)
    out = Path(args.out)
    src.save_raw(out)
    (out / "_meta.json").write_text(json.dumps({"recorded_at": now.isoformat()}), encoding="utf-8")
    _print({"saved": str(out), "files": len(list(out.glob('*.json')))})
    return 0


def cmd_macro_template(args, root):
    from .engine import load_strategy
    from .timeutil import trading_day_key
    import time
    cfg = load_strategy()
    day = trading_day_key(time.time(), cfg["sessions"]["trading_day_rollover"])
    p = root / "runs" / "macro" / f"{day}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    if not p.exists():
        tpl = {"date": day, "_how": "pairs.<คู่>.long/short = support | neutral | conflict (conflict = ห้ามเทรดทิศนั้นวันนี้) ใส่ conflict เฉพาะเมื่อขัดแย้งชัดเจน",
               "currencies": {c: "" for c in ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "XAU")},
               "pairs": {s: {"long": "neutral", "short": "neutral"} for s in cfg["symbols"]}, "sources": []}
        p.write_text(json.dumps(tpl, ensure_ascii=False, indent=1), encoding="utf-8")
    _print({"macro_file": str(p), "exists": True})
    return 0


def cmd_cot(args, root):
    from .cot import fetch, summary
    _print(summary(fetch(root)))
    return 0


def cmd_import_snapshot(args, root):
    """แตกไฟล์ snapshot รวม (จากเบราว์เซอร์/MCP) เป็นโฟลเดอร์ fixture แล้วพร้อมใช้กับ scan --fixtures"""
    src = Path(args.file)
    data = json.loads(src.read_text(encoding="utf-8"))
    stamp = (data.get("_meta", {}).get("captured_utc") or datetime.now(UTC).isoformat())[:16].replace(":", "").replace("-", "")
    out = root / "runs" / f"live-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    n = 0
    for k, v in data.items():
        if k.startswith("_"):
            continue
        (out / f"{k}.json").write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
        n += 1
    (out / "_meta.json").write_text(json.dumps(data.get("_meta", {}), ensure_ascii=False), encoding="utf-8")
    _print({"fixtures": str(out), "files": n, "captured_utc": data.get("_meta", {}).get("captured_utc"),
            "next": f"python3 -m mt5kit scan --fixtures {out} --mode plan_only"})
    return 0


def cmd_scan(args, root):
    from .engine import compact_summary, load_strategy, run_scan, select_symbol
    from .render import render
    import time
    symbol = getattr(args, "symbol", None)
    if symbol is not None:
        symbol = select_symbol(symbol, load_strategy())
    src = _source(args)
    now = _now(args) or time.time()
    slot = args.slot if args.slot != "auto" else _auto_slot(now)
    from .ledger import Ledger
    led = Ledger(root)
    with led.lock("scan"):
        run_dir, report = run_scan(root, src, args.mode, slot, now, symbol=symbol)
    html, issues = render(run_dir)
    out = compact_summary(report, run_dir)
    out["render_issues"] = issues
    _print(out)
    return 0


def cmd_render(args, root):
    from .render import render
    html, issues = render(root / "runs" / args.run)
    _print({"report_html": str(html), "issues": issues})
    return 0


def cmd_prepare(args, root):
    from .execution import ExecutionRefused, prepare_actions
    try:
        out = prepare_actions(root, _source(args), args.run, [p.strip() for p in args.plans.split(",") if p.strip()],
                              args.authorization, args.include_cancels, _now(args))
    except ExecutionRefused as e:
        _print({"refused": str(e)})
        return 2
    _print({"actions": [{k: a.get(k) for k in ("action_id", "type", "plan_id", "tool", "args", "expiry_th", "volume_note", "reason")} for a in out["actions"]],
            "refused": out["refused"],
            "next": "แสดงตารางนี้ให้ผู้ใช้ แล้วเรียกเครื่องมือ MCP ทีละ Action ตามลำดับ หลังแต่ละ Action รัน record-result"})
    return 0


def cmd_record_result(args, root):
    from .execution import record_result
    resp = json.loads(Path(args.response).read_text(encoding="utf-8")) if args.response else json.loads(args.json)
    _print(record_result(root, args.run, args.action, resp))
    return 0


def cmd_reconcile(args, root):
    from .execution import reconcile
    from .render import render
    ex = reconcile(root, _source(args), args.run)
    html, issues = render(root / "runs" / args.run)
    _print({"execution": ex, "report_html": str(html), "issues": issues})
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mt5kit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status")
    p = sub.add_parser("probe"); p.add_argument("--symbol", default="EURUSD")
    p = sub.add_parser("record"); p.add_argument("--out", required=True)
    sub.add_parser("macro-template")
    sub.add_parser("cot")
    p = sub.add_parser("import-snapshot"); p.add_argument("file")
    p = sub.add_parser("scan")
    p.add_argument("--symbol", help="เลือกสินทรัพย์เดียว เช่น XAUUSD หรือ EURUSD (ไม่ใส่ = ทั้งหมด)")
    p.add_argument("--mode", choices=["plan_only", "analyze_and_place"], default="plan_only")
    p.add_argument("--slot", choices=["auto", "london", "newyork", "manage"], default="auto")
    p.add_argument("--fixtures"); p.add_argument("--now")
    p = sub.add_parser("analyze", help="วิเคราะห์รายตัวและสร้างรายงาน โดยไม่ส่งคำสั่งซื้อขาย")
    p.add_argument("symbol", help="เช่น XAUUSD, EURUSD หรือ GOLD")
    p.set_defaults(mode="plan_only")
    p.add_argument("--slot", choices=["auto", "london", "newyork", "manage"], default="auto")
    p.add_argument("--fixtures"); p.add_argument("--now")
    p = sub.add_parser("render"); p.add_argument("--run", required=True)
    p = sub.add_parser("prepare-actions")
    p.add_argument("--run", required=True); p.add_argument("--plans", required=True)
    p.add_argument("--authorization", choices=["mode", "user"], required=True, help="mode = ผู้ใช้เลือกวางออเดอร์เลยตอนเริ่ม, user = ผู้ใช้สั่ง ส่ง {Plan ID}")
    p.add_argument("--include-cancels", action="store_true"); p.add_argument("--fixtures"); p.add_argument("--now")
    p = sub.add_parser("record-result")
    p.add_argument("--run", required=True); p.add_argument("--action", required=True)
    g = p.add_mutually_exclusive_group(required=True); g.add_argument("--response"); g.add_argument("--json")
    p = sub.add_parser("reconcile"); p.add_argument("--run", required=True); p.add_argument("--fixtures")
    args = ap.parse_args(argv)
    root = find_project_root()
    fn = {"status": cmd_status, "probe": cmd_probe, "record": cmd_record, "macro-template": cmd_macro_template, "cot": cmd_cot,
          "scan": cmd_scan, "analyze": cmd_scan, "import-snapshot": cmd_import_snapshot, "render": cmd_render, "prepare-actions": cmd_prepare, "record-result": cmd_record_result,
          "reconcile": cmd_reconcile}[args.cmd]
    try:
        return fn(args, root)
    except Exception as e:  # noqa: BLE001
        _print({"error": f"{type(e).__name__}: {e}"})
        return 1
