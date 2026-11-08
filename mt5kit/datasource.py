"""แหล่งข้อมูล: Live (MCP อ่านอย่างเดียว) หรือ Fixture (ไฟล์ JSON ที่บันทึกไว้) — ทั้งสองแบบผ่าน adapter ชุดเดียวกัน"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .mcp_client import McpClient

CONFIG_DIR = Path(__file__).parent / "config"


def load_map() -> dict:
    return json.loads((CONFIG_DIR / "mcp-map.json").read_text(encoding="utf-8"))


def _fill(template: Any, values: dict) -> Any:
    """แทน {key} ในแม่แบบ ถ้าทั้งสตริงเป็น {key} จะคงชนิดข้อมูลเดิม (เช่น ตัวเลข)"""
    if isinstance(template, dict):
        return {k: _fill(v, values) for k, v in template.items() if not (isinstance(v, str) and _missing(v, values))}
    if isinstance(template, list):
        return [_fill(v, values) for v in template]
    if isinstance(template, str):
        m = re.fullmatch(r"\{(\w+)\}", template)
        if m:
            return values.get(m.group(1))
        return re.sub(r"\{(\w+)\}", lambda mm: str(values.get(mm.group(1), mm.group(0))), template)
    return template


def _missing(v: str, values: dict) -> bool:
    keys = re.findall(r"\{(\w+)\}", v)
    return any(k not in values for k in keys)


class LiveSource:
    kind = "live"

    def __init__(self, client: McpClient | None = None, mcp_map: dict | None = None):
        self.map = mcp_map or load_map()
        self.client = client or McpClient.from_config(self.map.get("server", "terminal"))
        self.raw: dict[str, Any] = {}

    def call(self, logical: str, record_key: str | None = None, **values) -> Any:
        spec = self.map["read"][logical]
        args = _fill(spec.get("args", {}), values)
        data = self.client.call_tool(spec["tool"], args)
        self.raw[record_key or logical] = data
        return data

    def account(self):
        return self.call("account")

    def positions(self):
        return self.call("positions")

    def symbols(self):
        return self.call("symbols")

    def symbol_info(self, symbol: str):
        return self.call("symbol_info", f"symbol_{symbol}", symbol=symbol)

    def bars(self, symbol: str, tf: str, count: int):
        tfv = self.map.get("timeframes", {}).get(tf, tf)
        fmt = self.map.get("time_format", "%Y-%m-%dT%H:%M:%S")
        sec = {"M15": 900, "H1": 3600, "H4": 14400, "D1": 86400, "W1": 604800}[tf]
        now = datetime.utcnow() + timedelta(hours=3)          # เผื่อเวลาเซิร์ฟเวอร์ (UTC+2/+3)
        start = now - timedelta(seconds=count * sec * 1.45 + 3 * 86400)   # เผื่อวันหยุดสุดสัปดาห์
        end = now + timedelta(days=1)
        return self.call("history", f"bars_{symbol}_{tf}", symbol=symbol, tf=tfv, count=count,
                         **{"from": start.strftime(fmt), "to": end.strftime(fmt)})

    def time_info(self):
        return self.call("time")

    def calendar(self, t_from: datetime, t_to: datetime):
        """ค่าในปฏิทิน + ชื่อ/สกุลเงิน/ความสำคัญของ event (MT5 MCP แยกสองเครื่องมือ) → รวมเป็นรายการเดียว"""
        fmt = self.map.get("calendar_time_format", "%Y-%m-%dT%H:%M:%S")
        vals = self.client.call_tool(self.map["read"]["calendar"]["tool"],
                                     _fill(self.map["read"]["calendar"]["args"], {"from": t_from.strftime(fmt), "to": t_to.strftime(fmt)}))
        ev_spec = self.map["read"].get("calendar_events")
        if not ev_spec:
            self.raw["calendar"] = vals
            return vals
        evmap = {}
        for ccy in ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD"):
            try:
                ev = self.client.call_tool(ev_spec["tool"], _fill(ev_spec["args"], {"ccy": ccy}))
            except Exception:  # noqa: BLE001
                continue
            for e in (ev.get("events", []) if isinstance(ev, dict) else []):
                evmap[str(e.get("id"))] = (ccy, e.get("name", ""), e.get("importance", ""))
        rows = []
        for v in (vals.get("values", []) if isinstance(vals, dict) else []):
            m = evmap.get(str(v.get("event_id")))
            if not m:
                continue
            rows.append({"time": v.get("time"), "currency": m[0], "event": m[1], "importance": m[2],
                         "forecast": v.get("forecast_value") if v.get("forecast_value") is not None else "",
                         "previous": v.get("prev_value") if v.get("prev_value") is not None else "",
                         "actual": v.get("actual_value") if v.get("actual_value") is not None else ""})
        self.raw["calendar"] = rows
        return rows

    def history_orders(self, t_from: datetime, t_to: datetime):
        fmt = self.map.get("time_format", "%Y-%m-%dT%H:%M:%S")
        return self.call("history_orders", **{"from": t_from.strftime(fmt), "to": t_to.strftime(fmt)})

    def save_raw(self, out_dir: Path):
        out_dir.mkdir(parents=True, exist_ok=True)
        for k, v in self.raw.items():
            (out_dir / f"{k}.json").write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")


class FixtureSource:
    """อ่านไฟล์ที่บันทึกด้วยคำสั่ง record (ชื่อไฟล์ตรงกับ record_key)"""
    kind = "fixture"

    def __init__(self, folder: Path):
        self.dir = Path(folder)

    def _load(self, name: str):
        p = self.dir / f"{name}.json"
        if not p.exists():
            p = self.dir / f"{name.replace('#', '%23')}.json"   # ชื่อไฟล์ที่เข้ารหัส # ไว้
        if not p.exists():
            raise FileNotFoundError(f"fixture ไม่มีไฟล์ {name}.json")
        return json.loads(p.read_text(encoding="utf-8"))

    def account(self):
        return self._load("account")

    def positions(self):
        return self._load("positions")

    def symbols(self):
        return self._load("symbols")

    def symbol_info(self, symbol):
        try:
            return self._load(f"symbol_{symbol}")
        except FileNotFoundError:
            return None

    def bars(self, symbol, tf, count):
        return self._load(f"bars_{symbol}_{tf}")

    def time_info(self):
        try:
            return self._load("time")
        except FileNotFoundError:
            return None

    def calendar(self, t_from, t_to):
        return self._load("calendar")

    def history_orders(self, t_from, t_to):
        try:
            return self._load("history_orders")
        except FileNotFoundError:
            return []

    def save_raw(self, out_dir):
        pass


def server_now_from_time_info(payload) -> Any:
    """หาเวลาเซิร์ฟเวอร์จากผลของ get_time_information"""
    if payload is None:
        return None
    if isinstance(payload, dict):
        lk = {str(k).lower(): v for k, v in payload.items()}
        for key in ("server_time", "trade_server_time", "time_trade_server", "timetradeserver", "server", "time_current", "timecurrent"):
            if key in lk and lk[key]:
                return lk[key]
        for v in payload.values():
            r = server_now_from_time_info(v) if isinstance(v, dict) else None
            if r:
                return r
    return None


def default_range(now_utc: datetime, back_days: int, fwd_days: int):
    return now_utc - timedelta(days=back_days), now_utc + timedelta(days=fwd_days)
