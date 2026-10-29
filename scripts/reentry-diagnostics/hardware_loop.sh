#!/usr/bin/env bash
# Human-in-the-loop device reproduction, adapted from the diagnosing-bugs loop.
# Agent supplies the verified IPA commit; the user operates the real iOS app.
# Noninteractive form accepts the returned log and user observation from chat.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMMIT="${1:?verified diagnostic IPA commit required}"
LOG="${2:-}"
SAW_JUMP="${3:-unknown}"
OUT="${4:-${TMPDIR:-/tmp}/openminis-reentry-device-report.json}"
step() { printf '\n>>> %s\n' "$1"; read -r -p '    [Enter when done] ' _; }
capture() { local answer; printf '\n>>> %s\n' "$2"; read -r -p '    > ' answer; printf -v "$1" '%s' "$answer"; }
if [ -z "$LOG" ]; then
    step '用 TrollStore 覆盖安装本次诊断 IPA，不卸载、不清空原数据。'
    step '重新启动 Minis，打开原来会跳的会话，退出再进入，上下滑动一次再松手。'
    capture SAW_JUMP '这次是否看见原来的跳动？输入 yes / no / unknown。'
    step '在设置 → 日志中分享最新 reentry-*.log；不需要重新导出聊天。'
    capture LOG '输入收到的日志在分析主机上的绝对路径。'
fi
case "$SAW_JUMP" in yes|no|unknown) ;; *) printf 'invalid observation\n' >&2; exit 2;; esac
set +e
python3 "$ROOT/scripts/reentry-diagnostics/analyze_trace.py" log "$LOG" \
    --expected-commit "$COMMIT" --output "$OUT"
status=$?
set -e
python3 - "$OUT" "$SAW_JUMP" <<'PY'
import json,sys
from pathlib import Path
path=Path(sys.argv[1])
report=json.loads(path.read_text())
report['userObservedJump']=sys.argv[2]
report['deviceReproductionVerdict']='requires_geometry_attribution; not a fix acceptance'
path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print('USER_OBSERVED_JUMP='+sys.argv[2])
print('CAPTURE_STATUS='+report['status'])
print('REPORT='+str(path))
PY
exit "$status"
