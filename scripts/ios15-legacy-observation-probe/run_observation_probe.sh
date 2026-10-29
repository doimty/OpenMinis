#!/usr/bin/env bash
# Source-derived collection/legacy cache-observation probe (diagnostic, read-only).
# Usage: bash run_observation_probe.sh <repo-root> /absolute/output
# macOS only (Xcode 26.2 / build 17C52 / iOS 26.2 simulator, pinned).
set -euo pipefail
BASE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${1:?usage: bash run_observation_probe.sh <repo-root> /absolute/output}"
OUT="${2:?usage: bash run_observation_probe.sh <repo-root> /absolute/output}"
case "$OUT" in /*) ;; *) echo 'output must be absolute' >&2; exit 2 ;; esac
if [ -e "$OUT" ] && [ -n "$(ls -A "$OUT" 2>/dev/null)" ]; then
  echo 'INVALID: use a new/empty evidence directory' >&2; exit 2
fi
mkdir -p "$OUT"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
export BASE ROOT OUT BUILD
SDK="$(xcrun --sdk iphonesimulator --show-sdk-path)"
test "$(xcrun --sdk iphonesimulator --show-sdk-version)" = '26.2'
xcodebuild -version > "$OUT/xcode-version.txt"
grep -Fx 'Xcode 26.2' "$OUT/xcode-version.txt"
grep -Fx 'Build version 17C52' "$OUT/xcode-version.txt"
ARCH="$(uname -m)"
python3 "$BASE/prepare_observation_probe.py" "$BUILD/generated" --root "$ROOT" > "$OUT/prepare.log"
cp "$BUILD/generated/extraction.json" "$OUT/extraction.json"
for VARIANT in baseline candidate; do
  export VARIANT
  APP="$BUILD/ObservationProbe-$VARIANT.app"
  export APP
  mkdir -p "$APP" "$OUT/$VARIANT"
  python3 - <<'PY'
import os, plistlib, json, subprocess
from pathlib import Path
app = Path(os.environ['APP']); variant = os.environ['VARIANT']; out = Path(os.environ['OUT']) / variant
p = {'CFBundleExecutable': 'ObservationProbe', 'CFBundleIdentifier': 'com.openminis.observationprobe.' + variant,
     'CFBundleName': 'ObservationProbe', 'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1',
     'CFBundleShortVersionString': '1.0', 'MinimumOSVersion': '15.0', 'UIDeviceFamily': [1, 2],
     'UILaunchScreen': {}, 'UISupportedInterfaceOrientations': ['UIInterfaceOrientationPortrait']}
(app / 'Info.plist').write_bytes(plistlib.dumps(p))
(out / 'versions.json').write_text(json.dumps({
    'commit': subprocess.check_output(['git', '-C', os.environ['ROOT'], 'rev-parse', 'HEAD'], text=True).strip(),
    'xcode': subprocess.check_output(['xcodebuild', '-version'], text=True).splitlines(),
    'sdk': subprocess.check_output(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'], text=True).strip(),
    'arch': subprocess.check_output(['uname', '-m'], text=True).strip(),
    'limits': 'Forced-legacy production source on iOS26.2 runtime; not an iOS15 runtime or full-app acceptance.'}, indent=2) + '\n')
PY
  xcrun --sdk iphonesimulator swiftc -target "$ARCH-apple-ios15.0-simulator" \
    -sdk "$SDK" -swift-version 5 -parse-as-library \
    "$BUILD/generated/LegacyFlowLayout.swift" \
    "$BUILD/generated/LegacyHostingContent.swift" \
    "$BUILD/generated/MessageListLayout.swift" \
    "$BUILD/generated/ProductionInfrastructure-$VARIANT.swift" \
    "$BASE/ProbeApp.swift" \
    -o "$APP/ObservationProbe" > "$OUT/$VARIANT/compile.log" 2>&1 \
    || { cat "$OUT/$VARIANT/compile.log"; exit 2; }
  codesign --sign - "$APP"
  python3 - <<'PY'
import json, os, shutil, subprocess, sys, uuid
from pathlib import Path
base, root, out, app = (Path(os.environ[k]) for k in ('BASE', 'ROOT', 'OUT', 'APP'))
variant = os.environ['VARIANT']; out = out / variant
nonce = str(uuid.uuid4())
sys.path.insert(0, str(base))
from test_observation_probe import validate_report
(out / 'run-id.txt').write_text(nonce + '\n')
def sim(*args): return subprocess.check_output(['xcrun', 'simctl', *args], text=True).strip()
runtimes = json.loads(sim('list', 'runtimes', '--json'))['runtimes']
runtime = next((r for r in runtimes if r.get('version') == '26.2' and 'iOS' in r.get('identifier', '') and r.get('isAvailable')), None)
if runtime is None: raise SystemExit('INVALID: pinned iOS26.2 runtime unavailable')
devices = [d for d in json.loads(sim('list', 'devices', '--json'))['devices'].get(runtime['identifier'], []) if d.get('isAvailable') and 'iPhone' in d.get('name', '')]
if not devices: raise SystemExit('INVALID: no iPhone on pinned runtime')
device = sorted(devices, key=lambda d: ('Pro Max' not in d['name'], d['name']))[0]
udid = device['udid']
(out / 'simulator.json').write_text(json.dumps({'runtime': runtime, 'device': device}, indent=2) + '\n')
if device['state'] != 'Booted': sim('boot', udid)
subprocess.run(['xcrun', 'simctl', 'bootstatus', udid, '-b'], check=True, timeout=180)
bundle = 'com.openminis.observationprobe.' + variant
sim('install', udid, str(app))
env = os.environ.copy()
env['SIMCTL_CHILD_PROBE_RUN_ID'] = nonce
env['SIMCTL_CHILD_PROBE_VARIANT'] = variant
try:
    result = subprocess.run(['xcrun', 'simctl', 'launch', '--console', '--terminate-running-process', udid, bundle],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=100)
except subprocess.TimeoutExpired as error:
    text = error.stdout or b''
    if isinstance(text, bytes): text = text.decode(errors='replace')
    (out / 'console.log').write_text(text)
    raise SystemExit('INVALID: observation probe timed out')
(out / 'console.log').write_text(result.stdout)
container = Path(sim('get_app_container', udid, bundle, 'data'))
source = container / 'Documents' / 'observation-probe' / nonce
if source.exists(): shutil.copytree(source, out / 'results')
if not (source / 'report.json').is_file():
    print(result.stdout[-8000:]); raise SystemExit('INVALID: missing fresh native report')
report = json.loads((source / 'report.json').read_text())
validate_report(report, nonce, variant)
# simctl exit code is evidence only, never the verdict.
(out / 'summary.json').write_text(json.dumps({
    'variant': variant, 'run_id': nonce, 'simctl_exit': result.returncode,
    'controls_passed': report['controlsPassed'],
    'hypothesis_match': {s['case']: s.get('hypothesis_match') for s in report['samples'] if s.get('hypothesis_match') is not None}}, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False, indent=2))
print('PASS:', variant, 'report valid, controls green, hypothesis cases recorded')
PY
done
python3 - <<'PY'
import json, os
from pathlib import Path
out = Path(os.environ['OUT'])
a = json.loads((out / 'baseline/results/report.json').read_text())
b = json.loads((out / 'candidate/results/report.json').read_text())
assert a['controlsPassed'] and b['controlsPassed'], 'INVALID: a control case failed; hypothesis comparison is not meaningful'
assert {s['case'] for s in a['samples']} == {s['case'] for s in b['samples']}, 'INVALID: case lists differ between variants'
summary = {'observation_probe_complete': True,
           'baseline_variant': a['variant'], 'candidate_variant': b['variant'],
           'cases': sorted({s['case'] for s in a['samples']}),
           'baseline_hypothesis_match': {s['case']: s.get('hypothesis_match') for s in a['samples'] if s.get('hypothesis_match') is not None},
           'candidate_hypothesis_match': {s['case']: s.get('hypothesis_match') for s in b['samples'] if s.get('hypothesis_match') is not None},
           'limits': 'Cache/legacy observation semantics only; not a UI layout verdict.'}
(out / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary, indent=2))
PY