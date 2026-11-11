#!/bin/bash
# 安装/更新两个 launchd 定时任务：每分钟 tick（提醒 + 收信），早/午/晚简报。
# 用法：./install.sh [python3 路径]      卸载：./install.sh --uninstall
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
PY="${1:-$(command -v python3)}"
AGENTS="$HOME/Library/LaunchAgents"
UID_="$(id -u)"

if [ "${1:-}" = "--uninstall" ]; then
  for n in tick brief; do
    launchctl bootout "gui/$UID_/com.schedule-assistant.$n" 2>/dev/null || true
    rm -f "$AGENTS/com.schedule-assistant.$n.plist"
  done
  echo "已卸载"; exit 0
fi

[ -f "$ROOT/config.json" ] || { cp "$ROOT/config.example.json" "$ROOT/config.json"; echo "已生成 config.json，请先按需修改再重新运行"; exit 1; }
mkdir -p "$ROOT/logs" "$AGENTS"

# 简报时间取自 config.json 的 brief_times
TIMES=$("$PY" -c "import json;print(' '.join(json.load(open('$ROOT/config.json')).get('brief_times',['08:00','12:30','21:00'])))")
CAL=""
for t in $TIMES; do
  CAL="$CAL<dict><key>Hour</key><integer>$((10#${t%%:*}))</integer><key>Minute</key><integer>$((10#${t##*:}))</integer></dict>"
done

write() {  # $1=name $2=script $3=schedule-xml
  cat > "$AGENTS/com.schedule-assistant.$1.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.schedule-assistant.$1</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$ROOT/bin/$2</string></array>
  $3
  <key>StandardOutPath</key><string>$ROOT/logs/$1.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/$1.err.log</string>
  <key>EnvironmentVariables</key><dict><key>HOME</key><string>$HOME</string></dict>
</dict></plist>
EOF
  launchctl bootout "gui/$UID_/com.schedule-assistant.$1" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_" "$AGENTS/com.schedule-assistant.$1.plist"
}

write tick tick.py "<key>StartInterval</key><integer>60</integer>"
write brief brief.py "<key>StartCalendarInterval</key><array>$CAL</array>"
echo "已安装：tick（每分钟）、brief（${TIMES}），Python：${PY}"
