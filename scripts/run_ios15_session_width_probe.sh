#!/bin/bash
# Agent-runnable diagnostic probe. This is not a full OpenMinis build or
# device acceptance test. It compares the current and candidate intrinsic
# width contracts through the production legacy hosting bridge.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?usage: bash scripts/run_ios15_session_width_probe.sh /absolute/output}"
mkdir -p "$OUT"
BUILD="$(mktemp -d)"
export ROOT OUT BUILD

SDK="$(xcrun --sdk iphonesimulator --show-sdk-path)"
test "$(xcrun --sdk iphonesimulator --show-sdk-version)" = "26.2"
test "$(xcodebuild -version | sed -n '1p')" = "Xcode 26.2"
test "$(xcodebuild -version | sed -n '2p')" = "Build version 17C52"
ARCH="$(uname -m)"
RUN_ID="${PROBE_RUN_ID:-$(python3 -c 'import uuid; print(uuid.uuid4())')}"
export PROBE_RUN_ID="$RUN_ID"

GENERATED="$BUILD/generated"
python3 "$ROOT/scripts/ios15-session-width-probe/prepare_probe.py" \
  "$ROOT" "$GENERATED" | tee "$OUT/inputs-generated.json"
cp "$ROOT/scripts/ios15-session-width-probe/ProbeApp.swift" "$BUILD/ProbeApp.swift"

APP="$BUILD/SessionWidthProbe.app"
mkdir -p "$APP"
python3 - "$APP" "$RUN_ID" <<'PY'
import plistlib
import sys
from pathlib import Path
app = Path(sys.argv[1])
run_id = sys.argv[2]
info = {
    'CFBundleExecutable': 'SessionWidthProbe',
    'CFBundleIdentifier': 'com.openminis.session-width-probe',
    'CFBundleName': 'SessionWidthProbe',
    'CFBundlePackageType': 'APPL',
    'CFBundleVersion': run_id,
    'CFBundleShortVersionString': '1.0',
    'MinimumOSVersion': '15.0',
    'UIDeviceFamily': [1, 2],
    'UILaunchScreen': {},
    'UISupportedInterfaceOrientations': ['UIInterfaceOrientationPortrait'],
}
(app / 'Info.plist').write_bytes(plistlib.dumps(info))
PY

xcrun --sdk iphonesimulator clang -target "$ARCH-apple-ios15.0-simulator" \
  -isysroot "$SDK" -fobjc-arc \
  -c "$ROOT/src/ios/Shared/NSTextContainerSetSizeGuard.m" \
  -o "$BUILD/TextContainerGuard.o"

xcrun --sdk iphonesimulator swiftc -target "$ARCH-apple-ios15.0-simulator" \
  -sdk "$SDK" -swift-version 5 -parse-as-library \
  -import-objc-header "$ROOT/src/ios/Shared/NSTextContainerSetSizeGuard.h" \
  "$ROOT/src/ios/Shared/LegacyHostingContent.swift" \
  "$GENERATED/CodeBlockAttachment.swift" \
  "$BUILD/ProbeApp.swift" "$BUILD/TextContainerGuard.o" \
  -o "$APP/SessionWidthProbe" > "$OUT/compile.log" 2>&1 || {
    cat "$OUT/compile.log"
    exit 1
  }
codesign --sign - "$APP"

python3 - <<'PY'
import json, os, subprocess
from pathlib import Path
out = Path(os.environ['OUT'])
def run(*args):
    return subprocess.check_output(['xcrun', 'simctl', *args], text=True).strip()
runtimes = json.loads(run('list', 'runtimes', '--json'))['runtimes']
runtime = next((item for item in runtimes
                if item.get('version') == '26.2'
                and 'iOS' in item.get('identifier', '')
                and item.get('isAvailable')), None)
if runtime is None:
    raise SystemExit('BLOCKED: pinned iOS26.2 simulator runtime is not installed')
devices = json.loads(run('list', 'devices', '--json'))['devices'].get(runtime['identifier'], [])
devices = [item for item in devices if item.get('isAvailable')]
devices = [item for item in devices if 'Pro Max' in item['name'] or 'Plus' in item['name'] or 'iPad' in item['name']]
if not devices:
    raise SystemExit('BLOCKED: no available >=428pt device for the pinned runtime')
device = sorted(devices, key=lambda item: ('Pro Max' not in item['name'], item['name']))[0]
(out / 'simulator.json').write_text(json.dumps({'runtime': runtime, 'device': device}, indent=2) + '\n')
udid = device['udid']
if device['state'] != 'Booted':
    run('boot', udid)
subprocess.run(['xcrun', 'simctl', 'bootstatus', udid, '-b'], check=True, timeout=180)
run('install', udid, str(Path(os.environ['BUILD']) / 'SessionWidthProbe.app'))
command = ['xcrun', 'simctl', 'launch', '--console', '--terminate-running-process',
           udid, 'com.openminis.session-width-probe',
           f'--probe-run-id={os.environ["PROBE_RUN_ID"]}']
try:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=150)
except subprocess.TimeoutExpired as exc:
    output = exc.stdout or ''
    if isinstance(output, bytes):
        output = output.decode(errors='replace')
    (out / 'console.log').write_text(output)
    raise SystemExit('FAIL: native probe did not finish within 150 seconds')
(out / 'console.log').write_text(result.stdout)
data = Path(run('get_app_container', udid, 'com.openminis.session-width-probe', 'data'))
results = data / 'Documents/session-width-probe'
if results.exists():
    import shutil
    shutil.copytree(results, out / 'results', dirs_exist_ok=True)
report_path = out / 'results/report.json'
if result.returncode or not report_path.exists():
    print(result.stdout[-12000:])
    raise SystemExit('FAIL: probe crashed or did not produce report.json')
report = json.loads(report_path.read_text())
if report.get('os') != '26.2':
    raise SystemExit(f"FAIL: unexpected runtime {report.get('os')!r}")
if report.get('runID') != os.environ['PROBE_RUN_ID']:
    raise SystemExit(f"FAIL: report runID mismatch: {report.get('runID')!r}")
if len(report.get('samples', [])) != 24:
    raise SystemExit(f"FAIL: expected 24 samples, got {len(report.get('samples', []))}")
print('MATRIX_COMPLETE:', len(report['samples']))
print('CURRENT_FALLBACK_OVERFLOW:', report['currentFallbackReproducesOverflow'])
print('CANDIDATE_BOUNDED:', report['noWidthDemandFallbackBounded'])
print('VERDICT:', report['verdict'])
if report['verdict'] != 'candidate-suppresses-invalid-width-demand':
    raise SystemExit('DIAGNOSTIC_NOT_GREEN: inspect report.json; no production change is authorized by this result')
PY
