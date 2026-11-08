"""Ledger ในเครื่อง: แผน, Action, Equity ต้นวัน/สัปดาห์, จุดสูงสุด และ Lock กันรันชนกัน"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path

LOCK_STALE_SEC = 15 * 60


class LockError(RuntimeError):
    pass


class Ledger:
    def __init__(self, root: Path):
        self.dir = root / "ledger"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / "ledger.json"
        self.data = self._load()

    def _load(self) -> dict:
        if self.path.exists():
            return json.loads(self.path.read_text(encoding="utf-8"))
        return {"plans": {}, "actions": [], "equity_days": {}, "equity_weeks": {}, "hwm": None, "orders_by_day": {}, "started_at": None}

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)

    # ----- lock -----
    @contextmanager
    def lock(self, owner: str):
        lp = self.dir / ".lock"
        if lp.exists():
            try:
                info = json.loads(lp.read_text())
            except Exception:
                info = {"t": 0}
            if time.time() - info.get("t", 0) < LOCK_STALE_SEC:
                raise LockError(f"บัญชีถูกล็อกโดย {info.get('owner')} เมื่อ {int(time.time() - info.get('t', 0))} วินาทีก่อน — รอให้รอบนั้นจบ")
        lp.write_text(json.dumps({"owner": owner, "t": time.time(), "pid": os.getpid()}))
        try:
            yield
        finally:
            try:
                lp.unlink()
            except FileNotFoundError:
                pass

    # ----- equity tracking -----
    def track_equity(self, day_key: str, week_key: str, equity: float, now: float) -> dict:
        d = self.data
        d.setdefault("started_at", now)
        if not d.get("started_at"):
            d["started_at"] = now
        d["equity_days"].setdefault(day_key, equity)
        d["equity_weeks"].setdefault(week_key, equity)
        if d["hwm"] is None or equity > d["hwm"]:
            d["hwm"] = equity
        day0, week0, hwm = d["equity_days"][day_key], d["equity_weeks"][week_key], d["hwm"]
        return {
            "day_start_equity": day0, "week_start_equity": week0, "hwm": hwm,
            "day_pnl_frac": (equity - day0) / day0 if day0 else 0.0,
            "week_pnl_frac": (equity - week0) / week0 if week0 else 0.0,
            "drawdown": (hwm - equity) / hwm if hwm else 0.0,
            "history_since": d["started_at"],
        }

    def orders_today(self, day_key: str) -> int:
        return len(self.data["orders_by_day"].get(day_key, []))

    def note_order(self, day_key: str, plan_id: str):
        self.data["orders_by_day"].setdefault(day_key, [])
        if plan_id not in self.data["orders_by_day"][day_key]:
            self.data["orders_by_day"][day_key].append(plan_id)

    # ----- plans -----
    def put_plan(self, plan: dict):
        self.data["plans"][plan["plan_id"]] = plan

    def get_plan(self, plan_id: str) -> dict | None:
        p = self.data["plans"].get(plan_id)
        if p:
            return p
        # รองรับการอ้างแบบไม่มี revision
        cands = [v for k, v in self.data["plans"].items() if k.split(" ")[0] == plan_id.split(" ")[0]]
        return max(cands, key=lambda v: v.get("revision", 1)) if cands else None

    def owned_tickets(self) -> dict:
        out = {}
        for p in self.data["plans"].values():
            for tk in p.get("tickets", []):
                out[str(tk)] = p["plan_id"]
        return out

    def add_action(self, a: dict):
        self.data["actions"].append(a)


def make_plan_id(day: str, symbol: str, side: str, setup: str, bos_hhmm: str) -> str:
    return f"P-{day.replace('-', '')}-{symbol}-{'L' if side == 'long' else 'S'}-{setup.replace('+', '')}-{bos_hhmm}"
