# ตั้งค่าครั้งแรกและแก้ปัญหา

## 1. การเชื่อมต่อ MCP

- คัดลอก `.mcp.example.json` เป็น `.mcp.json` แล้วใส่ Token **ใหม่** (Token เก่าเคยหลุดขึ้น GitHub ต้องเปลี่ยนใน MT5)
- `.mcp.json` อยู่ใน `.gitignore` ห้าม commit
- สคริปต์อ่าน URL/Token จาก `.mcp.json` (server `terminal`) หรือ env `MT5_MCP_URL` / `MT5_MCP_TOKEN`

## 2. Probe — ปรับ mcp-map ให้ตรงกับเครื่องมือจริง

ชื่ออาร์กิวเมนต์ใน `mt5kit/config/mcp-map.json` ตอนนี้เป็นค่าที่เดา ต้องตรวจ:

```bash
python3 -m mt5kit probe --symbol EURUSD
```

1. เปิด `runs/probe/schemas.json` ดู input schema ของแต่ละเครื่องมือ
2. แก้ `read.*.args` และ `trade_templates.*.args` ใน `mcp-map.json` ให้ชื่อและรูปแบบตรง (เช่น `timeframe` เป็น `"H1"` หรือ `"PERIOD_H1"`, `count` หรือ `bars`)
3. แก้ `read.calendar.tool` ให้เป็นเครื่องมือในกลุ่ม `economic_calendar_*` ที่คืนค่าข่าวพร้อมเวลา/สกุลเงิน/ความสำคัญ (`calendar_tools` ในผล probe)
4. แก้ `order_types` ให้ตรงค่าที่ `trade_send_pending_order` ต้องการ
5. รัน probe ซ้ำจนทุกบรรทัดใน `results` เป็น `ok` แล้วตั้ง `"verified": true`
6. ถ้า adapter อ่านผลไม่ได้ ให้ดูไฟล์ `runs/probe/raw_*.json` แล้วแก้ `mt5kit/adapters.py` (เพิ่มชื่อฟิลด์) ห้ามเดาค่า

## 3. Profile

```bash
cp mt5kit/config/account-profile.example.json account-profile.json
```

ตรวจ: `expected_account_login`, `symbol_suffix` (เช่น `#`), `commission_per_lot_roundtrip`, `server_time.mode`
`execution.enabled` เป็น `false` จนกว่าจะผ่านการทดสอบบน Demo (ระยะที่ 4 ของ plan.md) และ `allow_live` เป็น `false` เสมอจนผู้ใช้สั่ง

## 4. บันทึก Fixture ไว้ทดสอบตอนตลาดปิด

```bash
python3 -m mt5kit record --out tests/fixtures/live-$(date +%Y%m%d)
python3 -m mt5kit scan --fixtures tests/fixtures/live-YYYYMMDD --now 2026-11-10T08:30:00Z
python3 -m unittest discover -s tests
```

## แก้ปัญหา

| อาการ | ทางแก้ |
|---|---|
| `เชื่อมต่อ MCP ไม่ได้` | เปิด MT5 และ MCP server ก่อน, ตรวจพอร์ตใน `.mcp.json` |
| `HTTP 401/403` | Token ผิดหรือหมดอายุ |
| `อ่านแท่งเทียนไม่ได้` | แก้ `read.history.args` หรือ adapter ตามผล probe |
| `Symbol: มีหลายชื่อ` | ตั้ง `symbol_overrides` ใน Profile เช่น `{"EURUSD": "EURUSD.m"}` |
| เวลาแท่งเพี้ยน | ตั้ง `server_time.mode` เป็น `ny_close` หรือ `fixed:+2` ตามโบรกเกอร์ |
| `บัญชีถูกล็อก` | มีรอบอื่นกำลังทำงาน รอ (ล็อกหมดอายุเองใน 15 นาที) |
