"""เวลา: แปลงเวลาเซิร์ฟเวอร์ MT5 → UTC และเช็กช่วงเวลา Session (รองรับ DST ด้วย zoneinfo)"""
from __future__ import annotations

import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc
NY = ZoneInfo("America/New_York")


def parse_server_time(v) -> datetime:
    """แปลงค่าเวลาจาก MT5 เป็น datetime แบบไม่มี tz (= เวลาเซิร์ฟเวอร์)
    MT5 เก็บ datetime เป็นวินาทีนับจาก 1970 ตามเวลาเซิร์ฟเวอร์ หรือเป็นข้อความ 'YYYY.MM.DD HH:MM[:SS]'"""
    if isinstance(v, (int, float)):
        x = float(v)
        if x > 1e11:  # มิลลิวินาที
            x /= 1000.0
        return datetime(1970, 1, 1) + timedelta(seconds=x)
    s = str(v).strip().replace("T", " ").replace("Z", "")
    s = re.sub(r"(\d{2}:\d{2})[+-]\d{2}:?\d{2}$", r"\1", s)
    s = re.sub(r"(\d{2}:\d{2}:\d{2})\.\d+$", r"\1", s)
    for fmt in ("%Y.%m.%d %H:%M:%S", "%Y.%m.%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y.%m.%d", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    if s.isdigit():
        return parse_server_time(int(s))
    raise ValueError(f"อ่านเวลาไม่ออก: {v!r}")


class ServerClock:
    """แปลงเวลาเซิร์ฟเวอร์ ↔ UTC
    mode: 'ny_close' (เซิร์ฟเวอร์ = เวลานิวยอร์ก + 7 ชม. แบบโบรกเกอร์ส่วนใหญ่) หรือ 'fixed' (offset คงที่)"""

    def __init__(self, mode: str = "ny_close", offset_hours: float = 2.0):
        self.mode = mode
        self.offset_hours = offset_hours

    @classmethod
    def from_profile(cls, cfg: dict | None, server_now=None, utc_now: datetime | None = None) -> "ServerClock":
        mode = (cfg or {}).get("mode", "auto")
        if mode.startswith("fixed:"):
            return cls("fixed", float(mode.split(":", 1)[1]))
        if mode == "ny_close":
            return cls("ny_close")
        # auto: เทียบเวลาเซิร์ฟเวอร์ปัจจุบันกับ UTC
        if server_now is None:
            return cls("ny_close")
        utc_now = utc_now or datetime.now(UTC)
        srv = parse_server_time(server_now)
        off = round(((srv - utc_now.replace(tzinfo=None)).total_seconds() / 3600) * 2) / 2
        ny_off = utc_now.astimezone(NY).utcoffset().total_seconds() / 3600
        if abs(off - (ny_off + 7)) < 0.01:
            return cls("ny_close")
        return cls("fixed", off)

    def to_utc(self, server_dt: datetime) -> datetime:
        if self.mode == "ny_close":
            ny_local = server_dt - timedelta(hours=7)
            return ny_local.replace(tzinfo=NY).astimezone(UTC)
        return (server_dt - timedelta(hours=self.offset_hours)).replace(tzinfo=UTC)

    def to_server(self, utc_dt: datetime) -> datetime:
        if self.mode == "ny_close":
            return (utc_dt.astimezone(NY).replace(tzinfo=None) + timedelta(hours=7))
        return utc_dt.astimezone(UTC).replace(tzinfo=None) + timedelta(hours=self.offset_hours)

    def describe(self) -> str:
        return "นิวยอร์ก+7 (เปลี่ยนตาม DST สหรัฐ)" if self.mode == "ny_close" else f"UTC{self.offset_hours:+g} คงที่"


def hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_window(ts_utc: float, win: dict) -> bool:
    """ts_utc อยู่ในช่วง [start, end] ของเวลาท้องถิ่นใน win หรือไม่"""
    tz = ZoneInfo(win["tz"])
    lt = datetime.fromtimestamp(ts_utc, UTC).astimezone(tz).time()
    return hm(win["start"]) <= lt <= hm(win["end"])


def window_bounds_utc(day_local: datetime.date, win: dict) -> tuple[float, float]:
    tz = ZoneInfo(win["tz"])
    a = datetime.combine(day_local, hm(win["start"]), tz).astimezone(UTC).timestamp()
    b = datetime.combine(day_local, hm(win["end"]), tz).astimezone(UTC).timestamp()
    return a, b


def local_date(ts_utc: float, tz_name: str):
    return datetime.fromtimestamp(ts_utc, UTC).astimezone(ZoneInfo(tz_name)).date()


def next_local_time_utc(ts_utc: float, tz_name: str, hhmm: str, weekday: int | None = None) -> float:
    """เวลา UTC ของ hh:mm (เวลาท้องถิ่น) ครั้งถัดไปหลัง ts_utc ถ้าระบุ weekday (0=จันทร์) จะหาวันนั้น"""
    tz = ZoneInfo(tz_name)
    lt = datetime.fromtimestamp(ts_utc, UTC).astimezone(tz)
    for add in range(0, 9):
        d = (lt + timedelta(days=add)).date()
        cand = datetime.combine(d, hm(hhmm), tz)
        if cand.timestamp() > ts_utc and (weekday is None or cand.weekday() == weekday):
            return cand.astimezone(UTC).timestamp()
    raise ValueError("หาเวลาไม่พบ")


def trading_day_key(ts_utc: float, rollover: dict) -> str:
    """วันทำการนับใหม่ทุก 17:00 นิวยอร์ก"""
    tz = ZoneInfo(rollover["tz"])
    lt = datetime.fromtimestamp(ts_utc, UTC).astimezone(tz)
    if lt.time() >= hm(rollover["time"]):
        lt = lt + timedelta(days=1)
    return lt.date().isoformat()


def fmt_local(ts_utc: float | None, tz_name: str = "Asia/Bangkok", fmt: str = "%d/%m %H:%M") -> str:
    if ts_utc is None:
        return "-"
    return datetime.fromtimestamp(ts_utc, UTC).astimezone(ZoneInfo(tz_name)).strftime(fmt)


def iso(ts_utc: float | None) -> str | None:
    return None if ts_utc is None else datetime.fromtimestamp(ts_utc, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
