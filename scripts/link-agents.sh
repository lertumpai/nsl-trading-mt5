#!/usr/bin/env bash
# สกิลตัวจริงอยู่ที่ .agents/skills (มาตรฐาน Agent Skills ที่ Codex และ agent หลายตัวอ่านได้เอง)
# สคริปต์นี้สร้าง symlink ให้ agent ที่อ่านจากโฟลเดอร์ของตัวเอง
#   bash scripts/link-agents.sh            → Claude Code (ค่าเริ่มต้น)
#   bash scripts/link-agents.sh all        → claude gemini cursor copilot
#   bash scripts/link-agents.sh claude gemini
set -euo pipefail
cd "$(dirname "$0")/.."
targets=("$@"); [ ${#targets[@]} -eq 0 ] && targets=(claude)
[ "${targets[0]}" = "all" ] && targets=(claude gemini cursor copilot)

link() {  # link <ลิงก์> <ปลายทางแบบ relative จากโฟลเดอร์ของลิงก์>
  local l="$1" t="$2"
  mkdir -p "$(dirname "$l")"
  if [ -L "$l" ]; then ln -sfn "$t" "$l"; echo "อัปเดต $l -> $t"
  elif [ -e "$l" ]; then echo "ข้าม $l (มีไฟล์/โฟลเดอร์จริงอยู่แล้ว ย้ายออกก่อนถ้าต้องการใช้ลิงก์)"
  else ln -s "$t" "$l"; echo "สร้าง $l -> $t"; fi
}

for a in "${targets[@]}"; do
  case "$a" in
    claude)  link .claude/skills ../.agents/skills; link CLAUDE.md AGENTS.md ;;
    gemini)  link .gemini/skills ../.agents/skills; link GEMINI.md AGENTS.md ;;
    cursor)  link .cursor/skills ../.agents/skills ;;
    copilot) link .github/skills ../.agents/skills ;;
    *) echo "ไม่รู้จัก: $a (ใช้ claude | gemini | cursor | copilot | all)"; exit 1 ;;
  esac
done
