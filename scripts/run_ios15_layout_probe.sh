#!/bin/bash
# Agent-runnable minimal native API-path probe. Requires the pinned Apple
# runner, NOT a full Minis build. A completed matrix is not app acceptance.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?usage: bash scripts/run_ios15_layout_probe.sh /absolute/output/directory}"
mkdir -p "$OUT"
BUILD="$(mktemp -d)"
export ROOT OUT BUILD
SDK="$(xcrun --sdk iphonesimulator --show-sdk-path)"
test "$(xcrun --sdk iphonesimulator --show-sdk-version)" = '26.2'
ARCH="$(uname -m)"

python3 - <<'PY'
import hashlib, json, os, plistlib, subprocess
from pathlib import Path
root, out, build = (Path(os.environ[k]) for k in ('ROOT', 'OUT', 'BUILD'))
source = root/'src/ios/Views/Chat/SelectableMarkdownView.swift'
text = source.read_text()
start = 'final class ThematicBreakAttachment: NSTextAttachment {'
end = '\n// MARK: - Math Attachment'
assert text.count(start) == 1 and text.count(end) == 1
excerpt = text[text.index(start):text.index(end, text.index(start))]
(build/'ThematicBreakAttachment.swift').write_text('import UIKit\n\n' + excerpt)
inputs = [source, root/'src/ios/Shared/LegacyHostingContent.swift',
          root/'src/ios/Shared/NSTextContainerSetSizeGuard.m',
          root/'scripts/ios15-layout-probe/ProbeApp.swift']
evidence = {
    'commit': subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip(),
    'xcode': subprocess.check_output(['xcodebuild', '-version'], text=True).strip().splitlines(),
    'simulator_sdk': subprocess.check_output(['xcrun', '--sdk', 'iphonesimulator', '--show-sdk-version'], text=True).strip(),
    'source_sha256': {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
    'attachment_excerpt_sha256': hashlib.sha256(excerpt.encode()).hexdigest(),
    'limits': 'Actual legacy host and attachment; primitive UITextView, not full app; iOS26.2 fallback bridge, not iOS15 runtime.',
}
(out/'inputs.json').write_text(json.dumps(evidence, indent=2)+'\n')
app = build/'LayoutProbe.app'
app.mkdir()
info = {
    'CFBundleExecutable': 'LayoutProbe', 'CFBundleIdentifier': 'com.openminis.layoutprobe',
    'CFBundleName': 'LayoutProbe', 'CFBundlePackageType': 'APPL',
    'CFBundleVersion': '1', 'CFBundleShortVersionString': '1.0',
    'MinimumOSVersion': '15.0', 'UIDeviceFamily': [1, 2],
    'UILaunchScreen': {}, 'UISupportedInterfaceOrientations': ['UIInterfaceOrientationPortrait'],
}
(app/'Info.plist').write_bytes(plistlib.dumps(info))
print(json.dumps(evidence, indent=2))
PY

xcrun --sdk iphonesimulator clang -target "$ARCH-apple-ios15.0-simulator" \
  -isysroot "$SDK" -fobjc-arc \
  -c "$ROOT/src/ios/Shared/NSTextContainerSetSizeGuard.m" -o "$BUILD/TextContainerGuard.o"
xcrun --sdk iphonesimulator swiftc -target "$ARCH-apple-ios15.0-simulator" \
  -sdk "$SDK" -swift-version 5 -parse-as-library \
  -import-objc-header "$ROOT/src/ios/Shared/NSTextContainerSetSizeGuard.h" \
  "$ROOT/src/ios/Shared/LegacyHostingContent.swift" \
  "$BUILD/ThematicBreakAttachment.swift" \
  "$ROOT/scripts/ios15-layout-probe/ProbeApp.swift" \
  "$BUILD/TextContainerGuard.o" -o "$BUILD/LayoutProbe.app/LayoutProbe" \
  > "$OUT/compile.log" 2>&1 || { cat "$OUT/compile.log"; exit 1; }
codesign --sign - "$BUILD/LayoutProbe.app"

python3 - <<'PY'
import json, os, shutil, subprocess
from pathlib import Path
out, build = (Path(os.environ[k]) for k in ('OUT', 'BUILD'))
def run(*args):
    return subprocess.check_output(['xcrun', 'simctl', *args], text=True).strip()
runtimes = json.loads(run('list', 'runtimes', '--json'))['runtimes']
runtime = next((r for r in runtimes if r.get('version') == '26.2'
                and 'iOS' in r.get('identifier', '') and r.get('isAvailable')), None)
if runtime is None:
    raise SystemExit('BLOCKED: pinned iOS26.2 simulator runtime is not installed; no silent runtime substitution')
devices = json.loads(run('list', 'devices', '--json'))['devices'].get(runtime['identifier'], [])
devices = [d for d in devices if d.get('isAvailable')]
# A Pro Max canvas fits the fixed 428pt pane; avoid a narrow simulated phone
# where the test viewport itself would extend beyond the window.
devices = [d for d in devices if 'Pro Max' in d['name'] or 'Plus' in d['name'] or 'iPad' in d['name']]
if not devices:
    raise SystemExit('BLOCKED: no available >=428pt device for the pinned runtime')
device = sorted(devices, key=lambda d: ('Pro Max' not in d['name'], d['name']))[0]
(out/'simulator.json').write_text(json.dumps({'runtime': runtime, 'device': device}, indent=2)+'\n')
udid = device['udid']
if device['state'] != 'Booted':
    run('boot', udid)
subprocess.run(['xcrun', 'simctl', 'bootstatus', udid, '-b'], check=True, timeout=180)
run('install', udid, str(build/'LayoutProbe.app'))
command = ['xcrun', 'simctl', 'launch', '--console', '--terminate-running-process', udid, 'com.openminis.layoutprobe']
try:
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, timeout=110)
except subprocess.TimeoutExpired as e:
    output = e.stdout or b''
    if isinstance(output, bytes): output = output.decode(errors='replace')
    (out/'console.log').write_text(output)
    raise SystemExit('FAIL: native probe did not finish within110 seconds')
(out/'console.log').write_text(result.stdout)
print('simulator_launch_exit', result.returncode)
data = Path(run('get_app_container', udid, 'com.openminis.layoutprobe', 'data'))
results = data/'Documents/layout-probe'
if results.exists():
    shutil.copytree(results, out/'results', dirs_exist_ok=True)
if result.returncode or not (results/'report.json').exists():
    print(result.stdout[-10000:])
    raise SystemExit('FAIL: probe crashed or did not produce its geometry report')
report = json.loads((results/'report.json').read_text())
assert report['os'] == '26.2', report['os']
assert len(report['samples']) == 35, len(report['samples'])
print('MATRIX_COMPLETE:', len(report['samples']), 'samples')
print('BASELINE_LIVE_OVERFLOW:', report['baseline_reproduces_live_width_overflow'])
print('PASSING_ABLATIONS:', report['passing_modes'])
for s in report['samples']:
    print(s['mode'], s['phase'], 'host=', s['host_width'], 'text=', s.get('text_width'),
          'height=', s.get('text_height'), 'expected=', s['expected_text_height'],
          'bounded=', s['bounded'], 'height_ok=', s['height_ok'])
if not report['baseline_reproduces_live_width_overflow']:
    print('INCONCLUSIVE FOR DEVICE: baseline did not reproduce; do not ship a fix on this evidence.')
PY
