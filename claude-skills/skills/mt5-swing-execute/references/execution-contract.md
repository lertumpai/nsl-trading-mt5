# สัญญาการส่งคำสั่ง (Execution contract)

## สถานะแผน

```text
PROPOSED → (prepare-actions) PREPARED → (เรียก trade_*) SUBMITTED / SUBMISSION_UNKNOWN / REJECTED
→ (reconcile) PENDING → FILLED → MANAGED → CLOSED
ทางออกอื่น: CANCELLED / EXPIRED
```

## ใครทำอะไร

| ขั้น | ผู้ทำ | ไฟล์ |
|---|---|---|
| ตรวจซ้ำ + เติมค่าคำสั่ง | `prepare-actions` | `runs/<id>/actions.json`, `ledger/ledger.json` |
| เรียก `trade_*` | โมเดล | — |
| บันทึกผลตอบกลับ | `record-result` | `runs/<id>/responses/<action>.json` |
| ยืนยันกับบัญชีจริง | `reconcile` | `report.json` → ส่วน `execution` |

## การตรวจซ้ำใน prepare-actions

- `execution.enabled = true` และบัญชีเป็น Demo (หรือ `allow_live = true` ที่ผู้ใช้ตั้งเอง) และ Login ตรง Profile
- แผนยังเป็น PROPOSED และเหลืออายุ ≥ 15 นาที
- ยังไม่ครบโควตาวันละ 2 (นับรวม SUBMISSION_UNKNOWN เพื่อความปลอดภัย)
- ไม่อยู่ในช่วงงดข่าว Tier 1 และไม่มีข่าว Tier 1 ก่อน Pending หมดอายุ
- ไม่มีไอเดียอื่นในคู่นั้น · ไม่มีไม้ที่คำนวณความเสี่ยงไม่ได้
- Buy Limit ต้องต่ำกว่า Ask / Sell Limit ต้องสูงกว่า Bid (เกิน Stops level)
- ราคายังไม่ย้อนมาแตะโซนเข้าหลัง BOS
- คำนวณล็อตใหม่จากงบปัจจุบัน ใช้ค่าที่ต่ำกว่าระหว่างแผนเดิมกับค่าใหม่

## การระบุเจ้าของไม้

- `comment` = `nsl <คู่><L|S><MMDDHHMM ของแท่ง BOS>` (≤ 31 ตัวอักษร) + `magic` จาก Profile
- Ledger เก็บ Ticket ที่ยืนยันแล้วต่อ Plan ID — ไม่พึ่ง comment อย่างเดียว (โบรกเกอร์อาจตัดข้อความ)

## ผลไม่แน่ชัด

ไม่มีทางรับประกันว่าส่งครั้งเดียวจาก RPC อย่างเดียว ถ้า Timeout ให้บันทึกผล → `reconcile` → ถ้ายังไม่พบออเดอร์ ให้ดูประวัติ (`get_trading_history_orders`) ก่อนตัดสินใจ ห้ามส่งซ้ำเพื่อ "ลองดู"
