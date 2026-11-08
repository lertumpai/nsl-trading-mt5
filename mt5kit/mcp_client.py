"""MCP client แบบอ่านอย่างเดียว (Streamable HTTP) ใช้แค่ไลบรารีมาตรฐานของ Python

กฎความปลอดภัย:
- เรียกได้เฉพาะเครื่องมืออ่านข้อมูล เครื่องมือที่เปลี่ยนแปลงบัญชีถูกปฏิเสธเสมอ
  (การส่งคำสั่งซื้อขายต้องให้โมเดลเรียกเครื่องมือ MCP เองหลังผู้ใช้ยืนยัน)
- ไม่พิมพ์ Token ลง Log หรือข้อความ Error
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ห้ามเรียกเครื่องมือที่ชื่อเข้าข่ายนี้ ไม่ว่ากรณีใด
FORBIDDEN_TOOL = re.compile(r"(^trade_|send|modify|delete|close|cancel|order_send|position_close|buy|sell)", re.I)


class McpError(RuntimeError):
    pass


class ForbiddenToolError(McpError):
    pass


def find_project_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for d in [p, *p.parents]:
        if (d / "mt5kit").is_dir() or (d / ".mcp.json").exists():
            return d
    return p


def load_server_config(server: str = "terminal", root: Path | None = None) -> tuple[str, dict]:
    """คืน (url, headers) จาก env หรือ .mcp.json"""
    url = os.environ.get("MT5_MCP_URL")
    token = os.environ.get("MT5_MCP_TOKEN")
    headers: dict[str, str] = {}
    if not url:
        cfg_path = find_project_root(root) / ".mcp.json"
        if not cfg_path.exists():
            raise McpError("ไม่พบ .mcp.json และไม่ได้ตั้ง MT5_MCP_URL (ดู .mcp.example.json)")
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        srv = cfg.get("mcpServers", {}).get(server)
        if not srv:
            raise McpError(f"ไม่พบ server '{server}' ใน .mcp.json")
        url = srv["url"]
        headers.update(srv.get("headers", {}))
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return url, headers


def _redact(text: str, headers: dict) -> str:
    for v in headers.values():
        if v and len(v) > 8:
            text = text.replace(v, "***")
            if v.lower().startswith("bearer "):
                text = text.replace(v[7:], "***")
    return text


class McpClient:
    def __init__(self, url: str, headers: dict | None = None, timeout: float = 30.0):
        self.url = url
        self.base_headers = dict(headers or {})
        self.timeout = timeout
        self.session_id: str | None = None
        self._id = 0
        self._initialized = False

    @classmethod
    def from_config(cls, server: str = "terminal", root: Path | None = None) -> "McpClient":
        url, headers = load_server_config(server, root)
        return cls(url, headers)

    # ---------- low level ----------
    def _post(self, payload: dict, expect_response: bool = True) -> Any:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            **self.base_headers,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers=headers, method="POST")
        # localhost ไม่ผ่าน proxy
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=self.timeout) as resp:
                sid = resp.headers.get("Mcp-Session-Id")
                if sid:
                    self.session_id = sid
                ctype = resp.headers.get("Content-Type", "")
                body = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            raise McpError(_redact(f"HTTP {e.code}: {detail}", self.base_headers)) from None
        except urllib.error.URLError as e:
            raise McpError(f"เชื่อมต่อ MCP ไม่ได้ ({self.url}): {e.reason} — เปิด MT5 และ MCP server แล้วหรือยัง?") from None
        if not expect_response or not body.strip():
            return None
        msgs = self._parse_body(body, ctype)
        want = payload.get("id")
        for m in msgs:
            if m.get("id") == want:
                if "error" in m:
                    raise McpError(_redact(json.dumps(m["error"], ensure_ascii=False), self.base_headers))
                return m.get("result")
        raise McpError("ไม่พบคำตอบที่ตรง id จาก MCP")

    @staticmethod
    def _parse_body(body: str, ctype: str) -> list[dict]:
        if "text/event-stream" in ctype or body.lstrip().startswith(("event:", "data:")):
            out, buf = [], []
            for line in body.splitlines():
                if line.startswith("data:"):
                    buf.append(line[5:].strip())
                elif not line.strip() and buf:
                    try:
                        out.append(json.loads("\n".join(buf)))
                    except json.JSONDecodeError:
                        pass
                    buf = []
            if buf:
                try:
                    out.append(json.loads("\n".join(buf)))
                except json.JSONDecodeError:
                    pass
            return out
        data = json.loads(body)
        return data if isinstance(data, list) else [data]

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def initialize(self) -> dict:
        res = self._post({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "initialize",
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "mt5kit", "version": "0.1.0"}},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"}, expect_response=False)
        self._initialized = True
        return res or {}

    def _ensure(self):
        if not self._initialized:
            self.initialize()

    # ---------- public ----------
    def list_tools(self) -> list[dict]:
        self._ensure()
        tools, cursor = [], None
        while True:
            params = {"cursor": cursor} if cursor else {}
            res = self._post({"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list", "params": params}) or {}
            tools.extend(res.get("tools", []))
            cursor = res.get("nextCursor")
            if not cursor:
                return tools

    def call_tool(self, name: str, arguments: dict | None = None) -> Any:
        """เรียกเครื่องมืออ่านข้อมูล คืนค่าที่แปลงแล้ว (JSON ถ้าแปลงได้ ไม่งั้นเป็นข้อความ)"""
        if FORBIDDEN_TOOL.search(name):
            raise ForbiddenToolError(f"mt5kit ห้ามเรียกเครื่องมือ '{name}' — การเปลี่ยนแปลงบัญชีต้องให้โมเดลเรียกเองหลังผู้ใช้ยืนยัน")
        self._ensure()
        res = self._post({"jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
                          "params": {"name": name, "arguments": arguments or {}}}) or {}
        if res.get("isError"):
            raise McpError(f"เครื่องมือ {name} ตอบ error: {extract_payload(res)}")
        return extract_payload(res)


def extract_payload(result: dict) -> Any:
    """แปลงผลลัพธ์ tools/call เป็นข้อมูล Python"""
    if not isinstance(result, dict):
        return result
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    texts = [c.get("text", "") for c in result.get("content", []) if c.get("type") == "text"]
    joined = "\n".join(texts).strip()
    if not joined:
        return result
    try:
        return json.loads(joined)
    except json.JSONDecodeError:
        # บางเครื่องมืออาจคืน JSON หลายก้อน
        parsed = []
        for t in texts:
            try:
                parsed.append(json.loads(t))
            except json.JSONDecodeError:
                return joined
        return parsed if len(parsed) != 1 else parsed[0]
