#!/usr/bin/env bash
# Diagnostic, deliberately red when production layout misses the visibility contract.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?usage: bash scripts/run_ios15_composer_probe.sh /absolute/output}"
case "$OUT" in /*) ;; *) echo 'output must be absolute' >&2; exit 2 ;; esac
mkdir -p "$OUT"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
export ROOT OUT BUILD
SDK="$(xcrun --sdk iphonesimulator --show-sdk-path)"
test "$(xcrun --sdk iphonesimulator --show-sdk-version)" = '26.2'
python3 "$ROOT/scripts/prepare_ios15_composer_probe.py" "$BUILD/generated"
cp "$BUILD/generated/extraction.json" "$OUT/extraction.json"
python3 - <<'PY'
import json, os, plistlib, subprocess
from pathlib import Path
root,out,build=(Path(os.environ[k]) for k in ('ROOT','OUT','BUILD'))
app=build/'ComposerProbe.app'; app.mkdir()
(app/'Info.plist').write_bytes(plistlib.dumps({
 'CFBundleExecutable':'ComposerProbe','CFBundleIdentifier':'com.openminis.composerprobe',
 'CFBundleName':'ComposerProbe','CFBundlePackageType':'APPL','CFBundleVersion':'1',
 'CFBundleShortVersionString':'1.0','MinimumOSVersion':'15.0','UIDeviceFamily':[1,2],
 'UILaunchScreen':{},'UISupportedInterfaceOrientations':['UIInterfaceOrientationPortrait']}))
(out/'versions.json').write_text(json.dumps({
 'commit':subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),
 'xcode':subprocess.check_output(['xcodebuild','-version'],text=True).strip(),
 'simulator_sdk':'26.2','limits':'Forced legacy source on iOS26.2, not an iOS15 runtime.'},indent=2)+'\n')
PY
xcrun --sdk iphonesimulator swiftc -target "$(uname -m)-apple-ios15.0-simulator" \
  -sdk "$SDK" -swift-version 5 -parse-as-library \
  "$ROOT/src/ios/Shared/LegacyFlowLayout.swift" \
  "$ROOT/src/ios/Shared/LegacyHostingContent.swift" \
  "$ROOT/src/ios/Agent/MessageList/MessageListLayout.swift" \
  "$BUILD/generated/ProductionInfrastructure.swift" \
  "$BUILD/generated/ProductionGrid.swift" \
  "$BUILD/generated/ProductionGeometry.swift" \
  "$BUILD/generated/ComposerFixture.swift" \
  "$ROOT/scripts/ios15-composer-probe/ProbeApp.swift" \
  -o "$BUILD/ComposerProbe.app/ComposerProbe" > "$OUT/compile.log" 2>&1 \
  || { cat "$OUT/compile.log"; exit 1; }
codesign --sign - "$BUILD/ComposerProbe.app"
python3 - <<'PY'
import json,os,shutil,subprocess
from pathlib import Path
out=Path(os.environ['OUT']); build=Path(os.environ['BUILD'])
def run(*args): return subprocess.check_output(['xcrun','simctl',*args],text=True).strip()
runtimes=json.loads(run('list','runtimes','--json'))['runtimes']
runtime=next((r for r in runtimes if r.get('version')=='26.2' and 'iOS' in r.get('identifier','') and r.get('isAvailable')),None)
if runtime is None: raise SystemExit('BLOCKED: pinned iOS26.2 runtime unavailable')
devices=json.loads(run('list','devices','--json'))['devices'].get(runtime['identifier'],[])
devices=[d for d in devices if d.get('isAvailable') and 'iPhone' in d['name']]
if not devices: raise SystemExit('BLOCKED: no iPhone on pinned runtime')
device=sorted(devices,key=lambda d:('Pro Max' not in d['name'],d['name']))[0]
(out/'simulator.json').write_text(json.dumps({'runtime':runtime,'device':device},indent=2)+'\n')
udid=device['udid']; bundle='com.openminis.composerprobe'
if device['state']!='Booted': run('boot',udid)
subprocess.run(['xcrun','simctl','bootstatus',udid,'-b'],check=True,timeout=180)
run('install',udid,str(build/'ComposerProbe.app'))
try:
 result=subprocess.run(['xcrun','simctl','launch','--console','--terminate-running-process',udid,bundle],
                       stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=120)
except subprocess.TimeoutExpired as e:
 output=e.stdout or b''
 if isinstance(output,bytes): output=output.decode(errors='replace')
 (out/'console.log').write_text(output)
 raise SystemExit('FAIL: composer probe timed out')
(out/'console.log').write_text(result.stdout)
data=Path(run('get_app_container',udid,bundle,'data'))/'Documents/composer-probe'
if data.exists(): shutil.copytree(data,out/'results',dirs_exist_ok=True)
report_path=out/'results/report.json'
if not report_path.exists():
 print(result.stdout[-8000:]); raise SystemExit('FAIL: missing native report')
report=json.loads(report_path.read_text())
print(json.dumps(report,ensure_ascii=False,indent=2))
if report.get('os')!='26.2': raise SystemExit('FAIL: unexpected runtime')
if result.returncode or not report['passed']: raise SystemExit('FAIL: actual production geometry contract failed; see report and screenshots')
print('PASS: production layout fixture on iOS26.2; device acceptance remains separate')
PY
