# Price Action Dataset: EURUSD, USDJPY, GBPUSD

รวบรวมช่วง 8 พฤศจิกายน 2016 ถึงแท่งล่าสุดที่ปิดแล้ว ณ 2026-11-06T23:57:00 เวลาเซิร์ฟเวอร์ MT5 เสร็จแล้ว: ประวัติ 13,771,322 แท่ง และช็อต/controls 31,104 เหตุการณ์ ข้อมูลต้นทางบางช่วงมีช่องว่าง ให้ดูรายงานก่อนใช้ฝึกโมเดล

## เริ่มอ่าน

1. [รายงานผลจริงและข้อจำกัด](REVIEW_REPORT.md)
2. [คำอธิบายคอลัมน์](DATA_DICTIONARY.md) หรือ [CSV dictionary](data_dictionary.csv)
3. [แนวทางให้ AI/นักพัฒนาทำงานต่อ](AI_HANDOFF.md)
4. [Schema ที่ส่งออกจริง](schema.json) และ [กฎคัดช็อต](config/selection_rules_v1.json)

## โครงสร้าง

```text
EURUSD/                         # USDJPY และ GBPUSD ใช้รูปแบบเดียวกัน
  history/2016-11/M1.csv         # M5.csv และ M15.csv อยู่โฟลเดอร์เดียวกัน
  warmup/2016-10/                # บริบทก่อนเริ่มช่วงหลัก แยกจากยอด 10 ปี
  events/YYYY-MM-DD/HH-mm-ss_pattern_side/
    M1.csv                      # 240 แท่งที่ปิดแล้ว
    M5.csv                      # 288 แท่งที่ปิดแล้ว
    M15.csv                     # 480 แท่งที่ปิดแล้ว
    event.csv                   # บริบท ประเภทรูปแบบ เหตุผลเลือก และ split
    levels.csv                  # ระดับราคาที่รู้แล้วพร้อมเวลายืนยัน
    plan.csv                    # แผน entry/SL/TP/expiry/time stop
    outcome.csv                 # ผลจำลองภายหลัง แยกจาก model inputs
  event_manifest.csv
  candidate_rejections.csv
history_manifest.csv
warmup_manifest.csv
manifest.csv
audit/
```

ทุก TF ในช็อตใช้เวลาตัดสินใจเดียวกัน แต่ความยาวบริบทย้อนหลังต่างกัน ใช้เฉพาะแท่งปิด เช่น 14:32 ใช้ M1 ปิดถึง 14:32 และ M5/M15 ปิดถึง 14:30

## การใช้ข้อมูล

- `history` เป็นราคาดิบ native จาก MT5 แยกคู่และเดือน เก็บค่าเดิมพร้อม checksum ไม่เติมแท่งปลอม
- `events` เป็นหน้าต่างที่คัดจาก 4 รูปแบบราคาและ no-setup controls ไม่เลือกจากผลตอบแทน
- `review_status=unreviewed` หมายถึงยังไม่มีคนตรวจความสวยของทุกช็อต ตัวคัดเป็นกฎอัตโนมัติที่ทำซ้ำได้
- ไม่มี UTC ที่เดาเอง `*_server` เป็นเวลาบนกราฟโบรกเกอร์ ช่อง UTC/offset ที่ยังไม่ยืนยันเว้นว่าง
- `tick_volume` ไม่ใช่ปริมาณรวมทั้งตลาด FX และ `real_volume` ว่างเพราะ MCP history ไม่ส่งฟิลด์นี้
- `outcome.csv` ใช้จำลอง M1 OHLC และ spread ของแท่ง ขาดข้อมูล tick/cost จริง จึงไม่ใช่ผลการซื้อขายที่ยืนยันแล้ว
- ข้อมูล `pattern_label`, `side_label`, `plan.csv` เป็นคำตอบสำหรับ supervised learning ส่วน `outcome.csv` เป็นข้อมูลอนาคต ห้ามอ่านรวมทุก CSV เป็น features โดยอัตโนมัติ
- Controls ถูกสุ่มด้วยกฎ hash ที่อัตรา 0.0005 และสัญญาณถูก deduplicate อัตราส่วนชนะ/แพ้หรือความถี่ใน event dataset จึงไม่ใช่ความถี่ของทุกนาทีในตลาด ต้องประเมินแบบ replay บน raw history ภายหลัง
- `schema_v1/` และ ZIP แบบร่างเป็นเอกสารออกแบบก่อน extraction ให้ใช้ schema/dictionary ที่ root เป็นหลัก

## สถานะงาน

เสร็จ: ดึงประวัติครบคำขอ 9 ชุด ตรวจ source gaps สร้าง features/ช็อต แผนเข้าออก ผลจำลองที่แยก provenance ตรวจไฟล์และเวลา และเขียนเอกสาร

ยังไม่ได้ทำ: รีวิวทุกช็อตด้วยสายตา, เก็บ real ticks ของทุกช็อต, ยืนยัน historical UTC/costs, ฝึกโมเดล, ทดสอบกำไรสุทธิ, สร้าง EA หรือส่งคำสั่งเทรด

## แหล่งอ้างอิง MT5

- [MqlRates](https://www.mql5.com/en/docs/constants/structures/mqlrates)
- [CopyRates และข้อจำกัดประวัติ](https://www.mql5.com/en/docs/series/copyrates)
- [Real and Generated Ticks](https://www.metatrader5.com/en/terminal/help/algotrading/tick_generation)
- [การตั้งค่า Max bars](https://www.metatrader5.com/en/terminal/help/startworking/settings)
