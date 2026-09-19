#!/usr/bin/env bash
# Execute the production legacy hosting path on the pinned simulator and check
# whether a short cell estimate makes the footer Retry area touch-dead.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:?usage: bash scripts/run_ios15_retry_probe.sh /absolute/output}"
mkdir -p "$OUT"
BUILD="$(mktemp -d)"
trap 'rm -rf "$BUILD"' EXIT
export ROOT OUT BUILD
SDK="$(xcrun --sdk iphonesimulator --show-sdk-path)"
test "$(xcrun --sdk iphonesimulator --show-sdk-version)" = '26.2'
ARCH="$(uname -m)"
python3 - <<'PY'
import hashlib,json,os,plistlib,subprocess
from pathlib import Path
root,out,build=(Path(os.environ[k]) for k in ('ROOT','OUT','BUILD'))
inputs=[root/'src/ios/Shared/LegacyHostingContent.swift',root/'scripts/ios15-retry-probe/ProbeApp.swift']
ev={'commit':subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip(),'xcode':subprocess.check_output(['xcodebuild','-version'],text=True).strip().splitlines(),'simulator_sdk':'26.2','source_sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},'limits':'Production legacy host on iOS26.2; not an iOS15 runtime.'}
(out/'inputs.json').write_text(json.dumps(ev,indent=2)+'\n')
app=build/'RetryProbe.app';app.mkdir()
info={'CFBundleExecutable':'RetryProbe','CFBundleIdentifier':'com.openminis.retryprobe','CFBundleName':'RetryProbe','CFBundlePackageType':'APPL','CFBundleVersion':'1','CFBundleShortVersionString':'1.0','MinimumOSVersion':'15.0','UIDeviceFamily':[1,2],'UILaunchScreen':{},'UISupportedInterfaceOrientations':['UIInterfaceOrientationPortrait']}
(app/'Info.plist').write_bytes(plistlib.dumps(info))
PY
xcrun --sdk iphonesimulator swiftc -target "$ARCH-apple-ios15.0-simulator" \
  -sdk "$SDK" -swift-version 5 -parse-as-library \
  "$ROOT/src/ios/Shared/LegacyHostingContent.swift" \
  "$ROOT/scripts/ios15-retry-probe/ProbeApp.swift" \
  -o "$BUILD/RetryProbe.app/RetryProbe" > "$OUT/compile.log" 2>&1 \
  || { cat "$OUT/compile.log"; exit 1; }
codesign --sign - "$BUILD/RetryProbe.app"
python3 - <<'PY'
import json,os,shutil,subprocess
from pathlib import Path
out,build=(Path(os.environ[k]) for k in ('OUT','BUILD'))
def run(*args):return subprocess.check_output(['xcrun','simctl',*args],text=True).strip()
runtimes=json.loads(run('list','runtimes','--json'))['runtimes']
runtime=next((r for r in runtimes if r.get('version')=='26.2' and 'iOS' in r.get('identifier','') and r.get('isAvailable')),None)
if runtime is None:raise SystemExit('BLOCKED: pinned iOS26.2 simulator runtime unavailable')
devices=json.loads(run('list','devices','--json'))['devices'].get(runtime['identifier'],[])
devices=[d for d in devices if d.get('isAvailable') and 'iPhone' in d['name']]
if not devices:raise SystemExit('BLOCKED: no iPhone on pinned runtime')
device=sorted(devices,key=lambda d:('Pro Max' not in d['name'],d['name']))[0]
(out/'simulator.json').write_text(json.dumps({'runtime':runtime,'device':device},indent=2)+'\n')
udid=device['udid']
if device['state']!='Booted':run('boot',udid)
subprocess.run(['xcrun','simctl','bootstatus',udid,'-b'],check=True,timeout=180)
run('install',udid,str(build/'RetryProbe.app'))
command=['xcrun','simctl','launch','--console','--terminate-running-process',udid,'com.openminis.retryprobe']
try:
 result=subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=100)
except subprocess.TimeoutExpired as e:
 output=e.stdout or b''
 if isinstance(output,bytes):output=output.decode(errors='replace')
 (out/'console.log').write_text(output)
 raise SystemExit('FAIL: native retry probe timed out')
(out/'console.log').write_text(result.stdout)
data=Path(run('get_app_container',udid,'com.openminis.retryprobe','data'))/'Documents/retry-probe'
if data.exists():shutil.copytree(data,out/'results',dirs_exist_ok=True)
if not (data/'report.json').exists():
 print(result.stdout[-8000:]);raise SystemExit('FAIL: missing native retry report')
report=json.loads((data/'report.json').read_text())
print(json.dumps(report,indent=2,ensure_ascii=False))
assert report['os']=='26.2',report['os']
assert result.returncode==0 and report['passed'],report.get('conclusion','retry probe failed')
print('PASS: native retry hit-test matrix (forced legacy host on iOS26.2)')
PY