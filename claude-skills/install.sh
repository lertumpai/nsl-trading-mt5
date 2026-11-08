#!/usr/bin/env bash
# ติดตั้งสกิลและ subagent ลงใน .claude/ ของโปรเจกต์นี้ (รันจากรากโปรเจกต์: bash claude-skills/install.sh)
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p .claude/skills .claude/agents
cp -R claude-skills/skills/mt5-swing-analyze claude-skills/skills/mt5-swing-execute .claude/skills/
cp claude-skills/agents/macro-news.md .claude/agents/
echo "ติดตั้งแล้ว: .claude/skills/mt5-swing-analyze, .claude/skills/mt5-swing-execute, .claude/agents/macro-news.md"
echo "ถ้าแก้สกิล ให้แก้ใน claude-skills/ แล้วรันสคริปต์นี้ซ้ำ"
