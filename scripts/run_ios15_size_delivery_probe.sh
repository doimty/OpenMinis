#!/usr/bin/env bash
# Run real LegacyHostingContentView delivery code on pinned Apple runtime.
# This is NOT a screenshot or UICollectionView reproduction.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?usage: bash scripts/run_ios15_size_delivery_probe.sh /absolute/output}"
case "$OUT" in /*) ;; *) echo 'output must be absolute' >&2; exit 2;; esac
export ROOT OUT
python3 - <<'PY'
import os
from pathlib import Path
out=Path(os.environ['OUT'])
if out.exists() and any(out.iterdir()):
    raise SystemExit('INVALID: output directory must be new or empty; no evidence is deleted')
out.mkdir(parents=True,exist_ok=True)
PY
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
export BUILD
SDK="$(xcrun --sdk iphonesimulator --show-sdk-path)"
test "$(xcrun --sdk iphonesimulator --show-sdk-version)" = '26.2'
xcodebuild -version | tee "$OUT/xcode-version.txt"
grep -Fx 'Xcode 26.2' "$OUT/xcode-version.txt"
grep -Fx 'Build version 17C52' "$OUT/xcode-version.txt"
ARCH="$(uname -m)"
python3 "$ROOT/scripts/prepare_ios15_size_delivery_probe.py" --output "$BUILD/generated"
cp "$BUILD/generated/inputs.json" "$OUT/inputs.json"
APP="$BUILD/SizeDeliveryProbe.app"
export APP
mkdir -p "$APP"
python3 - <<'PY'
import json, os, plistlib, subprocess
from pathlib import Path
app=Path(os.environ['APP']); out=Path(os.environ['OUT'])
p={'CFBundleExecutable':'SizeDeliveryProbe','CFBundleIdentifier':'com.openminis.SizeDeliveryProbe',
   'CFBundleName':'SizeDeliveryProbe','CFBundlePackageType':'APPL','CFBundleVersion':'1',
   'CFBundleShortVersionString':'1.0','MinimumOSVersion':'15.0','UIDeviceFamily':[1,2],
   'UILaunchScreen':{},'UISupportedInterfaceOrientations':['UIInterfaceOrientationPortrait']}
(app/'Info.plist').write_bytes(plistlib.dumps(p))
v={'xcode':subprocess.check_output(['xcodebuild','-version'],text=True).splitlines(),
   'sdk':subprocess.check_output(['xcrun','--sdk','iphonesimulator','--show-sdk-version'],text=True).strip(),
   'arch':subprocess.check_output(['uname','-m'],text=True).strip()}
(out/'versions.json').write_text(json.dumps(v,indent=2)+'\n')
PY
if ! xcrun --sdk iphonesimulator swiftc -target "$ARCH-apple-ios15.0-simulator" \
  -sdk "$SDK" -swift-version 5 -parse-as-library \
  "$BUILD/generated/LegacyHostingContentTestable.swift" \
  "$ROOT/scripts/ios15-size-delivery-probe/ProbeApp.swift" \
  -o "$APP/SizeDeliveryProbe" > "$OUT/compile.log" 2>&1; then
  cat "$OUT/compile.log"
  echo 'INVALID: native compilation failed; no behavioral verdict' >&2
  exit 2
fi
codesign --sign - "$APP"
python3 - <<'PY'
import json, os, shutil, subprocess, sys, uuid
from pathlib import Path
sys.path.insert(0,str(Path(os.environ['ROOT'])/'scripts'))
from prepare_ios15_size_delivery_probe import validate_native_report
out=Path(os.environ['OUT']); app=Path(os.environ['APP'])
run_id=str(uuid.uuid4())
(out/'run-id.txt').write_text(run_id+'\n')
def sim(*args):
    return subprocess.check_output(['xcrun','simctl',*args],text=True).strip()
runtimes=json.loads(sim('list','runtimes','--json'))['runtimes']
runtime=next((r for r in runtimes if r.get('version')=='26.2' and 'iOS' in r.get('identifier','') and r.get('isAvailable')),None)
if runtime is None:
    raise SystemExit('INVALID: pinned iOS26.2 runtime not available')
devices=json.loads(sim('list','devices','--json'))['devices'].get(runtime['identifier'],[])
devices=[d for d in devices if d.get('isAvailable') and 'iPhone' in d.get('name','')]
if not devices:
    raise SystemExit('INVALID: no iPhone on pinned runtime')
device=sorted(devices,key=lambda d:('Pro Max' not in d['name'], d['name']))[0]
udid=device['udid']
(out/'simulator.json').write_text(json.dumps({'runtime':runtime,'device':device},indent=2)+'\n')
if device['state']!='Booted': sim('boot',udid)
subprocess.run(['xcrun','simctl','bootstatus',udid,'-b'],check=True,timeout=180)
sim('install',udid,str(app))
env=os.environ.copy(); env['SIMCTL_CHILD_PROBE_RUN_ID']=run_id
command=['xcrun','simctl','launch','--console','--terminate-running-process',udid,'com.openminis.SizeDeliveryProbe']
process_code=None; timed_out=False
try:
    result=subprocess.run(command,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=90)
    output=result.stdout; process_code=result.returncode
except subprocess.TimeoutExpired as exc:
    timed_out=True
    output=exc.stdout or b''
    if isinstance(output,bytes): output=output.decode(errors='replace')
(out/'console.log').write_text(output)
container=Path(sim('get_app_container',udid,'com.openminis.SizeDeliveryProbe','data'))
source=container/'Documents'/'size-delivery-probe'/run_id
if source.exists(): shutil.copytree(source,out/'results')
report_path=source/'report.json'
if timed_out or not report_path.is_file():
    print(output[-6000:])
    raise SystemExit('INVALID: timeout or missing fresh native report')
report=json.loads(report_path.read_text())
try:
    validate_native_report(report,run_id)
except (ValueError,TypeError,KeyError) as error:
    raise SystemExit(f'INVALID: {error}')
print(json.dumps(report,ensure_ascii=False,indent=2))
summary={'run_id':run_id,'simctl_exit_code':process_code,'controls_passed':report['controlsPassed'],
         'case_count':len(report['cases']),'failed_cases':[x['name'] for x in report['cases'] if not x['passed']],
         'scope':'real component-delivery contract; NOT screenshot reproduction'}
(out/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
if not report['controlsPassed']:
    raise SystemExit('INVALID: basic callback controls failed')
if not report['passed']:
    raise SystemExit('FAIL: production notification contract violated (not a screenshot root-cause verdict)')
if process_code!=0:
    raise SystemExit('INVALID: native process failed after reporting success')
print('PASS: component notification matrix; UI screenshot problem not adjudicated')
PY
