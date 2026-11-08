"""สร้าง report.html จาก report.json + narrative.json (โมเดลไม่ต้องเขียน HTML)"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ASSETS = Path(__file__).parent / "assets"
MAX_OVERVIEW = 600
MAX_REASON = 200


class RenderError(ValueError):
    pass


def merge_narrative(report: dict, run_dir: Path) -> list[str]:
    """อ่าน narrative.json (ส่วนเดียวที่โมเดลเขียน) ตรวจความยาว และห้ามมีตัวเลขราคา/ล็อตที่ไม่ตรงกับแผน"""
    issues = []
    p = run_dir / "narrative.json"
    if not p.exists():
        return issues
    nar = json.loads(p.read_text(encoding="utf-8"))
    ov = str(nar.get("overview", ""))[:MAX_OVERVIEW]
    per = {}
    valid_ids = {x["plan_id"] for x in report.get("plans", [])}
    allowed_nums = set()
    for pl in report.get("plans", []):
        for k in ("entry", "sl", "tp", "volume"):
            if pl.get(k) is not None:
                allowed_nums.add(_norm(pl[k]))
    for pid, items in (nar.get("per_plan") or {}).items():
        if pid not in valid_ids:
            issues.append(f"narrative อ้าง plan_id ที่ไม่มี: {pid}")
            continue
        clean = []
        for t in list(items)[:3]:
            t = str(t)[:MAX_REASON]
            for num in re.findall(r"\d+\.\d{3,}", t):
                if _norm(num) not in allowed_nums:
                    issues.append(f"ตัวเลข {num} ใน narrative ไม่ตรงกับแผน — ตัดออก")
                    t = t.replace(num, "…")
            clean.append(t)
        per[pid] = clean
    # per_watch: คำอธิบายเพื่อการเรียนรู้รายคู่ (ไม่บังคับ) ตัวเลขต้องตรงกับระดับที่โค้ดคำนวณ
    watch_nums = set()
    for w in report.get("watch", []):
        for lv in w.get("levels", []):
            watch_nums.add(_norm(round(lv["price"], w.get("digits", 5))))
        for z in w.get("zones", []):
            watch_nums.add(_norm(round(z["top"], w.get("digits", 5)))); watch_nums.add(_norm(round(z["bottom"], w.get("digits", 5))))
    valid_watch = {w["symbol"] for w in report.get("watch", [])}
    per_watch = {}
    for sym, text in (nar.get("per_watch") or {}).items():
        if sym not in valid_watch:
            issues.append(f"narrative อ้างคู่ที่ไม่มีในส่วนเฝ้าดู: {sym}")
            continue
        t = str(text)[:MAX_OVERVIEW]
        for num in re.findall(r"\d+\.\d{3,}", t):
            if _norm(num) not in watch_nums:
                issues.append(f"ตัวเลข {num} ใน per_watch[{sym}] ไม่ตรงกับระดับที่คำนวณ — ตัดออก")
                t = t.replace(num, "…")
        per_watch[sym] = t
    report["narrative"] = {"overview": ov, "per_plan": per, "per_watch": per_watch,
                           "macro_note": str(nar.get("macro_note", ""))[:MAX_OVERVIEW]}
    return issues


def _norm(v) -> str:
    try:
        return f"{float(v):.5f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(v)


def tz_shift(report: dict) -> int:
    tz = ZoneInfo(report.get("report_tz", "Asia/Bangkok"))
    return int(datetime.fromtimestamp(report["generated_at"], timezone.utc).astimezone(tz).utcoffset().total_seconds())


def verify(report: dict, html: str) -> list[str]:
    """ตรวจหลังเรนเดอร์: ข้อมูลแผนครบ ไม่มี Token ไม่มีเลขบัญชีเต็ม"""
    problems = []
    for p in report.get("plans", []):
        for k in ("entry", "sl", "tp", "volume", "expiry_utc"):
            if p.get(k) in (None, 0, 0.0) and k != "tp":
                problems.append(f"{p['plan_id']}: ไม่มีค่า {k}")
        if p.get("tp") is None:
            problems.append(f"{p['plan_id']}: ไม่มี TP")
        if p["plan_id"] not in report.get("charts", {}):
            problems.append(f"{p['plan_id']}: ไม่มีข้อมูลกราฟ")
        if p["side"] == "long" and not (p["sl"] < p["entry"] < p["tp"]):
            problems.append(f"{p['plan_id']}: ลำดับราคา Long ผิด")
        if p["side"] == "short" and not (p["tp"] < p["entry"] < p["sl"]):
            problems.append(f"{p['plan_id']}: ลำดับราคา Short ผิด")
    if re.search(r"Bearer\s+[A-Za-z0-9+/=]{12,}", html):
        problems.append("พบ Token ใน HTML")
    return problems


def render(run_dir: Path) -> tuple[Path, list[str]]:
    run_dir = Path(run_dir)
    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    issues = merge_narrative(report, run_dir)
    report["tz_shift_sec"] = tz_shift(report)
    tpl = (ASSETS / "report-template.html").read_text(encoding="utf-8")
    lib = (ASSETS / "lightweight-charts.standalone.production.js").read_text(encoding="utf-8")
    data = json.dumps(report, ensure_ascii=False, default=str).replace("</", "<\\/")
    html = tpl.replace("/*__LWC__*/", lib.replace("</script", "<\\/script")).replace("/*__REPORT_JSON__*/null", data)
    problems = verify(report, html)
    if problems:
        issues += problems
    out = run_dir / "report.html"
    out.write_text(html, encoding="utf-8")
    (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return out, issues
