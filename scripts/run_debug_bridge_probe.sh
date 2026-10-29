#!/usr/bin/env bash
# Real Objective-C/Swift bridge lookup and dispatch, baseline vs candidate.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?usage: bash scripts/run_debug_bridge_probe.sh /absolute/output}"
case "$OUT" in /*) ;; *) echo 'output must be absolute' >&2; exit 2;; esac
export ROOT OUT
python3 - <<'PY'
import os
from pathlib import Path
p=Path(os.environ['OUT'])
if p.exists() and any(p.iterdir()): raise SystemExit('INVALID: use a new/empty evidence directory')
p.mkdir(parents=True,exist_ok=True)
PY
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
export BUILD
SDK="$(xcrun --sdk iphonesimulator --show-sdk-path)"
test "$(xcrun --sdk iphonesimulator --show-sdk-version)" = '26.2'
xcodebuild -version > "$OUT/xcode-version.txt"
grep -Fx 'Xcode 26.2' "$OUT/xcode-version.txt"
grep -Fx 'Build version 17C52' "$OUT/xcode-version.txt"
ARCH="$(uname -m)"
python3 "$ROOT/scripts/prepare_debug_bridge_probe.py" --output "$BUILD/sources" > "$OUT/prepare.log"
cp "$BUILD/sources/inputs.json" "$OUT/inputs.json"
for VARIANT in baseline candidate; do
  export VARIANT
  APP="$BUILD/DebugBridgeProbe-$VARIANT.app"
  export APP
  mkdir -p "$APP" "$OUT/$VARIANT"
  python3 - <<'PY'
import os,plistlib,json,subprocess
from pathlib import Path
app=Path(os.environ['APP']); variant=os.environ['VARIANT']; out=Path(os.environ['OUT'])/variant
p={'CFBundleExecutable':'DebugBridgeProbe','CFBundleIdentifier':'com.openminis.DebugBridgeProbe.'+variant,
   'CFBundleName':'DebugBridgeProbe','CFBundlePackageType':'APPL','CFBundleVersion':'1',
   'CFBundleShortVersionString':'1.0','MinimumOSVersion':'15.0','UIDeviceFamily':[1,2],
   'UILaunchScreen':{},'UISupportedInterfaceOrientations':['UIInterfaceOrientationPortrait']}
(app/'Info.plist').write_bytes(plistlib.dumps(p))
v={'xcode':subprocess.check_output(['xcodebuild','-version'],text=True).splitlines(),
   'sdk':subprocess.check_output(['xcrun','--sdk','iphonesimulator','--show-sdk-version'],text=True).strip(),
   'arch':subprocess.check_output(['uname','-m'],text=True).strip(),'swift_module':'Minis'}
(out/'versions.json').write_text(json.dumps(v,indent=2)+'\n')
PY
  xcrun --sdk iphonesimulator clang -target "$ARCH-apple-ios15.0-simulator" -isysroot "$SDK" \
    -fobjc-arc -I "$ROOT/scripts/ios15-debug-bridge-probe" \
    -c "$BUILD/sources/$VARIANT/ProbeDispatcher.m" -o "$BUILD/$VARIANT-dispatch.o" \
    > "$OUT/$VARIANT/objc-compile.log" 2>&1 \
    || { cat "$OUT/$VARIANT/objc-compile.log"; exit 2; }
  xcrun --sdk iphonesimulator swiftc -target "$ARCH-apple-ios15.0-simulator" -sdk "$SDK" \
    -swift-version 5 -D DEBUG -module-name Minis -parse-as-library \
    -import-objc-header "$ROOT/scripts/ios15-debug-bridge-probe/BridgeProbe.h" \
    "$BUILD/sources/$VARIANT/DebugLocalDispatch.swift" \
    "$BUILD/sources/$VARIANT/MinisDebugLogReader.swift" \
    "$ROOT/scripts/ios15-debug-bridge-probe/ProbeStubs.swift" \
    "$ROOT/scripts/ios15-debug-bridge-probe/ProbeApp.swift" "$BUILD/$VARIANT-dispatch.o" \
    -o "$APP/DebugBridgeProbe" > "$OUT/$VARIANT/swift-compile.log" 2>&1 \
    || { cat "$OUT/$VARIANT/swift-compile.log"; exit 2; }
  codesign --sign - "$APP"
  python3 - <<'PY'
import json,os,shutil,subprocess,sys,uuid
from pathlib import Path
root=Path(os.environ['ROOT']); out=Path(os.environ['OUT'])/os.environ['VARIANT']; app=Path(os.environ['APP'])
variant=os.environ['VARIANT']; nonce=str(uuid.uuid4())
sys.path.insert(0,str(root/'scripts'))
from prepare_debug_bridge_probe import validate_report
(out/'run-id.txt').write_text(nonce+'\n')
def sim(*args): return subprocess.check_output(['xcrun','simctl',*args],text=True).strip()
runtimes=json.loads(sim('list','runtimes','--json'))['runtimes']
runtime=next((r for r in runtimes if r.get('version')=='26.2' and 'iOS' in r.get('identifier','') and r.get('isAvailable')),None)
if runtime is None: raise SystemExit('INVALID: pinned simulator unavailable')
devices=[d for d in json.loads(sim('list','devices','--json'))['devices'].get(runtime['identifier'],[]) if d.get('isAvailable') and 'iPhone' in d.get('name','')]
if not devices: raise SystemExit('INVALID: no iPhone on pinned runtime')
device=sorted(devices,key=lambda d:('Pro Max' not in d['name'],d['name']))[0]; udid=device['udid']
(out/'simulator.json').write_text(json.dumps({'runtime':runtime,'device':device},indent=2)+'\n')
if device['state']!='Booted': sim('boot',udid)
subprocess.run(['xcrun','simctl','bootstatus',udid,'-b'],check=True,timeout=180)
bundle='com.openminis.DebugBridgeProbe.'+variant
sim('install',udid,str(app))
env=os.environ.copy();env['SIMCTL_CHILD_PROBE_RUN_ID']=nonce;env['SIMCTL_CHILD_PROBE_VARIANT']=variant
try:
    result=subprocess.run(['xcrun','simctl','launch','--console','--terminate-running-process',udid,bundle],env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=75)
except subprocess.TimeoutExpired as error:
    text=error.stdout or b''
    if isinstance(text,bytes):text=text.decode(errors='replace')
    (out/'console.log').write_text(text)
    raise SystemExit('INVALID: native bridge probe timed out')
(out/'console.log').write_text(result.stdout)
container=Path(sim('get_app_container',udid,bundle,'data'))
source=container/'Documents'/'debug-bridge-probe'/nonce
if source.exists():shutil.copytree(source,out/'results')
if not (source/'report.json').is_file():
    print(result.stdout[-6000:]);raise SystemExit('INVALID: missing fresh native report')
report=json.loads((source/'report.json').read_text())
validate_report(report,nonce,variant)
if variant=='candidate' and result.returncode!=0:raise SystemExit('INVALID: candidate process failed after report')
(out/'summary.json').write_text(json.dumps({'variant':variant,'run_id':nonce,'simctl_exit':result.returncode,
    'controls_passed':report['controlsPassed'],'bridge_ready':report['passed'],
    'expected_baseline_failure':variant=='baseline'},indent=2)+'\n')
print(json.dumps(report,indent=2))
print('PASS:',variant,'met the unchanged native probe expectation')
PY
done
python3 - <<'PY'
import os,json
from pathlib import Path
out=Path(os.environ['OUT'])
a=json.loads((out/'baseline/results/report.json').read_text())
b=json.loads((out/'candidate/results/report.json').read_text())
assert a['controlsPassed'] and not a['passed'] and b['controlsPassed'] and b['passed']
report={'native_red_green_verified':True,'baseline_runtime_name':a['runtimeDispatcherName'],
        'candidate_runtime_name':b['runtimeDispatcherName'],'candidate_c_dispatch_reached_rpc':b['backgroundDispatcherReachedRPC'],
        'limits':'Bridge name/dispatch validation only, not footer rendering or log-reading behavior.'}
(out/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps(report,indent=2))
PY
