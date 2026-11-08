"""COT (Traders in Financial Futures) จาก CFTC Public Reporting Environment — ใช้เป็นบริบทเท่านั้น ไม่เพิ่มคะแนน

dataset id ยังไม่ได้ยืนยันจากเครื่องนี้ ถ้าดึงไม่ได้ให้รายงานว่า "ไม่มีข้อมูล COT" ห้ามสร้างข้อมูลแทน
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

DATASET_URL = "https://publicreporting.cftc.gov/resource/gpe5-46if.json"  # TFF Futures Only (ตรวจอีกครั้งก่อนใช้)
MARKETS = {
    "EUR": "EURO FX", "GBP": "BRITISH POUND", "JPY": "JAPANESE YEN", "AUD": "AUSTRALIAN DOLLAR",
    "NZD": "NZ DOLLAR", "CAD": "CANADIAN DOLLAR",
}
CACHE_DAYS = 3


def fetch(root: Path, weeks: int = 52) -> dict:
    cache = root / "runs" / "cache" / "cot.json"
    if cache.exists() and time.time() - cache.stat().st_mtime < CACHE_DAYS * 86400:
        return json.loads(cache.read_text(encoding="utf-8"))
    out = {"source": DATASET_URL, "fetched_at": time.time(), "currencies": {}, "error": None}
    try:
        for ccy, mk in MARKETS.items():
            q = {"$where": f"market_and_exchange_names like '{mk}%'", "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": str(weeks)}
            url = DATASET_URL + "?" + urllib.parse.urlencode(q)
            with urllib.request.urlopen(url, timeout=20) as r:
                rows = json.loads(r.read().decode())
            series = []
            for row in rows:
                am = _f(row.get("asset_mgr_positions_long")) - _f(row.get("asset_mgr_positions_short"))
                lf = _f(row.get("lev_money_positions_long")) - _f(row.get("lev_money_positions_short"))
                series.append({"report_date": row.get("report_date_as_yyyy_mm_dd", "")[:10], "asset_mgr_net": am, "lev_funds_net": lf})
            out["currencies"][ccy] = series
    except Exception as e:  # noqa: BLE001 — ดึงไม่ได้ = ไม่มีข้อมูล
        out["error"] = f"ดึง COT ไม่ได้: {type(e).__name__}"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def summary(cot: dict) -> dict:
    """สรุปต่อสกุลเงิน: สถานะสุทธิล่าสุด และการเปลี่ยนแปลง 4 สัปดาห์ (ทิศของ Futures = ทิศสกุลเงินเทียบ USD)"""
    if not cot or cot.get("error") or not cot.get("currencies"):
        return {"available": False, "note": (cot or {}).get("error") or "ไม่มีข้อมูล COT"}
    out = {"available": True, "by_ccy": {}}
    for ccy, s in cot["currencies"].items():
        if len(s) < 5:
            continue
        out["by_ccy"][ccy] = {
            "report_date": s[0]["report_date"],
            "asset_mgr_net": s[0]["asset_mgr_net"], "asset_mgr_chg4w": s[0]["asset_mgr_net"] - s[4]["asset_mgr_net"],
            "lev_funds_net": s[0]["lev_funds_net"], "lev_funds_chg4w": s[0]["lev_funds_net"] - s[4]["lev_funds_net"],
        }
    return out
