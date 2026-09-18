#!/usr/bin/env bash
# Bounded, read-only iOS 15 native-floor probe for RealTimeCutVADCXXLibrary.
#
# Initial gate: device arm64 source rebuild against pinned inputs. Everything
# runs in $RUNNER_TEMP; the repository is never written. Downloads happen only
# on the macOS runner and only for the two pinned input zips. Evidence is
# collected even when a step fails; the job exits nonzero on any failed gate.
# This probe publishes no framework, no IPA, and changes no app code.
set -u

# This probe is macOS-only: it builds with Xcode's clang/xcodebuild and
# inspects Mach-O with otool. Refuse to run elsewhere so a Linux host can
# never create partial evidence directories or touch the network.
case "$(uname -s)" in
  Darwin) ;;
  *) printf 'error: ios15-vad-probe requires macOS (uname=%s)\n' "$(uname -s)" >&2; exit 2 ;;
esac

WORK="${RUNNER_TEMP:?}/vad-probe"
mkdir -p "$WORK" "$WORK/fragments"
: > "$WORK/status.txt"
: > "$WORK/probe.log"

note() { printf '%s\n' "$*" | tee -a "$WORK/probe.log"; }
gate() { # name rc: accumulate a gate result without aborting
  printf '%s=%s\n' "$1" "$2" >> "$WORK/status.txt"
  if [ "$2" -eq 0 ]; then note "gate ok: $1"; else note "GATE FAILED: $1 (rc=$2)"; fi
}
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    shasum -a 256 "$1" | awk '{print $1}'
  fi
}

# Evidence is assembled on EVERY exit path, including early gate failures.
summarize() {
  python3 - "$WORK" <<'PY'
import json, os, sys
from pathlib import Path
work = Path(sys.argv[1])
status = {}
for line in (work / 'status.txt').read_text().splitlines():
    key, value = line.split('=', 1)
    status[key] = int(value)
evidence = {
    'probe': 'ios15-vad-probe',
    'status': status,
    'approved': all(value == 0 for value in status.values()),
    'device_arm64_only': True,
    'slices_built': ['iphoneos/arm64'],
    'inputs': {
        'onnxruntime_sha256': os.environ.get('ONNX_SHA256', ''),
        'webrtc_apm_sha256': os.environ.get('APM_SHA256', ''),
        'native_source_sha': os.environ.get('NATIVE_CXX_SHA', ''),
        'wrapper_source_sha': os.environ.get('WRAPPER_CXX_SHA', ''),
    },
}
for path in ('xcode-version.txt', 'sdk-version.txt', 'settings.txt',
             'inputs-audit.json', 'inputs-structure.json', 'out/build.log',
             'out/output-audit.json', 'out/output-otool-l.txt',
             'out/output-otool-Iv.txt', 'out/output-otool-L.txt',
             'out/output-file.txt', 'out/output-binary.sha256',
             'wrapper-build/compile.log', 'wrapper-build/VADWrapper.o.sha256'):
    file_path = work / path
    if file_path.exists():
        evidence[path.replace('/', '_')] = file_path.read_text(
            errors='replace')[:200000]
evidence_dir = work / 'evidence'
evidence_dir.mkdir(exist_ok=True)
(evidence_dir / 'evidence.json').write_text(
    json.dumps(evidence, indent=1) + '\n')
print('VERDICT: ' + ('APPROVED' if evidence['approved'] else 'NOT APPROVED'))
for key, value in status.items():
    print('  %s: %s' % (key, 'ok' if value == 0 else 'FAIL(%d)' % value))
PY
}
trap summarize EXIT

export DEVELOPER_DIR="${DEVELOPER_DIR:?}"

# ------------------------------------------------------------ toolchain ----
note '=== toolchain ==='
"$DEVELOPER_DIR/usr/bin/xcodebuild" -version | tee "$WORK/xcode-version.txt"
xcrun --sdk iphoneos --show-sdk-version > "$WORK/sdk-version.txt" 2>&1
note "iphoneos SDK: $(cat "$WORK/sdk-version.txt")"
TOOLCHAIN_RC=0
grep -q "^Xcode ${EXPECTED_XCODE:-}$" "$WORK/xcode-version.txt" || TOOLCHAIN_RC=1
grep -q "Build version ${EXPECTED_XCODE_BUILD:-}$" "$WORK/xcode-version.txt" || TOOLCHAIN_RC=1
[ "$(cat "$WORK/sdk-version.txt")" = "${EXPECTED_IOS_SDK:-}" ] || TOOLCHAIN_RC=1
gate toolchain "$TOOLCHAIN_RC"
[ "$TOOLCHAIN_RC" -eq 0 ] || exit 1

# ------------------------------------------------------------- clone pins ----
note '=== clone pinned sources (read-only, shallow) ==='
NATIVE_SHA="${NATIVE_CXX_SHA:?}"
WRAPPER_SHA="${WRAPPER_CXX_SHA:?}"
mkdir -p "$WORK/src"
export GIT_TERMINAL_PROMPT=0

git clone --quiet --filter=blob:none --no-checkout \
  https://github.com/helloooideeeeea/RealTimeCutVADCXXLibrary.git \
  "$WORK/src/native" 2>"$WORK/src/native-clone.log"
git -C "$WORK/src/native" fetch --quiet --depth 1 origin "$NATIVE_SHA" \
  2>>"$WORK/src/native-clone.log"
git -C "$WORK/src/native" checkout --quiet "$NATIVE_SHA"
CLONE_NATIVE_RC=0
[ "$(git -C "$WORK/src/native" rev-parse HEAD)" = "$NATIVE_SHA" ] || CLONE_NATIVE_RC=1
gate clone_native "$CLONE_NATIVE_RC"

git clone --quiet --filter=blob:none --no-checkout \
  https://github.com/helloooideeeeea/RealTimeCutVADLibrary.git \
  "$WORK/src/wrapper" 2>"$WORK/src/wrapper-clone.log"
git -C "$WORK/src/wrapper" fetch --quiet --depth 1 origin "$WRAPPER_SHA" \
  2>>"$WORK/src/wrapper-clone.log"
git -C "$WORK/src/wrapper" checkout --quiet "$WRAPPER_SHA"
CLONE_WRAPPER_RC=0
[ "$(git -C "$WORK/src/wrapper" rev-parse HEAD)" = "$WRAPPER_SHA" ] || CLONE_WRAPPER_RC=1
gate clone_wrapper "$CLONE_WRAPPER_RC"
{ [ "$CLONE_NATIVE_RC" -eq 0 ] && [ "$CLONE_WRAPPER_RC" -eq 0 ]; } || exit 1

# --------------------------------------------------- download inputs (Mac) ----
note '=== download pinned native inputs (macOS runner only) ==='
mkdir -p "$WORK/inputs" "$WORK/native/Frameworks"
ONNX_ZIP="$WORK/inputs/onnxruntime.xcframework.zip"
APM_ZIP="$WORK/inputs/webrtc_audio_processing.xcframework.zip"
curl -fsSL --retry 2 --max-time 900 -o "$ONNX_ZIP" "${ONNX_URL:?}"
curl -fsSL --retry 2 --max-time 900 -o "$APM_ZIP" "${APM_URL:?}"
ONNX_SHA="$(sha256_file "$ONNX_ZIP")"
APM_SHA="$(sha256_file "$APM_ZIP")"
note "onnxruntime sha256=$ONNX_SHA"
note "webrtc_apm  sha256=$APM_SHA"
DOWNLOAD_RC=0
[ "$ONNX_SHA" = "${ONNX_SHA256:?}" ] || DOWNLOAD_RC=1
[ "$APM_SHA" = "${APM_SHA256:?}" ] || DOWNLOAD_RC=1
gate downloads "$DOWNLOAD_RC"
[ "$DOWNLOAD_RC" -eq 0 ] || exit 1

python3 - "$ONNX_ZIP" "$APM_ZIP" "$WORK/native/Frameworks" <<'PY'
import sys, zipfile
from pathlib import Path
for archive, dest in [(sys.argv[1], Path(sys.argv[3])),
                      (sys.argv[2], Path(sys.argv[3]))]:
    with zipfile.ZipFile(archive) as zipped:
        zipped.extractall(dest)
        print('extracted %s -> %s' % (archive, dest))
PY

# --------------------------------------------------- structural inputs -------
note '=== structural probe: xcframeworks ==='
python3 - "$WORK/native/Frameworks" > "$WORK/inputs-structure.json" <<'PY'
import json, sys
from pathlib import Path
root = Path(sys.argv[1])
out = []
for xcf in sorted(root.glob('*.xcframework')):
    slices = []
    for fw in sorted([p for p in xcf.glob('*/*.framework')]):
        binaries = [str(p) for p in fw.rglob('*') if p.is_file()
                    and p.name != 'Info.plist']
        slices.append({'slice': fw.relative_to(xcf).parts[0],
                       'framework': str(fw), 'binaries': binaries})
    out.append({'xcframework': xcf.name, 'slices': slices})
print(json.dumps(out, indent=1))
PY
STRUCT_RC=0
grep -q 'ios-arm64' "$WORK/inputs-structure.json" || STRUCT_RC=1
gate inputs_structure "$STRUCT_RC"
[ "$STRUCT_RC" -eq 0 ] || exit 1

# ------------------------------------------------------ audit inputs --------
note '=== audit selected input slices (device arm64) ==='
PROBE_SCRIPTS="$(pwd)/scripts" python3 - \
  "$WORK/native/Frameworks" > "$WORK/inputs-audit.json" <<'PY'
import json, os, plistlib, subprocess, sys
from pathlib import Path
root = Path(sys.argv[1])
sys.path.insert(0, os.environ['PROBE_SCRIPTS'])
from audit_ios15_native_floor import audit_image

def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stdout

def parse_minos(value):
    parts = [int(part) for part in str(value).split('.')]
    return (parts[0], parts[1] if len(parts) > 1 else 0)

report = []
all_ok = True
for xcf in sorted(root.glob('*.xcframework')):
    for fw in sorted(xcf.glob('ios-arm64/*.framework')):
        entry = {'xcframework': xcf.name, 'slice': 'ios-arm64',
                 'framework': str(fw)}
        plist = None
        plist_path = fw / 'Info.plist'
        if plist_path.exists():
            try:
                plist = plistlib.loads(plist_path.read_bytes())
            except Exception as exc:
                plist = {'_error': str(exc)}
        entry['plist'] = plist
        binary = None
        executable = (plist or {}).get('CFBundleExecutable')
        if executable:
            candidate = fw / executable
            if candidate.exists():
                binary = candidate
        if binary is None:
            for candidate in sorted(fw.rglob('*')):
                if candidate.is_file() and candidate.name != 'Info.plist':
                    binary = candidate
                    break
        if binary is None:
            entry['errors'] = ['no binary found in slice']
            all_ok = False
            report.append(entry)
            continue
        entry['binary'] = str(binary)
        file_text = run(['file', str(binary)])
        otool_text = run(['otool', '-l', '-arch', 'all', str(binary)])
        audit = audit_image(otool_text, file_text, str(binary))
        entry.update(audit)
        entry['file'] = file_text.strip()
        is_static = 'current ar archive' in file_text
        plist_min = (plist or {}).get('MinimumOSVersion')
        if plist is not None and plist_min is not None:
            if parse_minos(plist_min) > (15, 0):
                entry['errors'].append(
                    'Info.plist MinimumOSVersion %s > 15.0' % plist_min)
        elif not is_static:
            entry['errors'].append(
                'Info.plist missing MinimumOSVersion for dynamic framework')
        if entry['errors']:
            all_ok = False
        report.append(entry)
print(json.dumps({'all_ok': all_ok, 'report': report}, indent=1))
PY
INPUTS_RC=0
python3 -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1]))["all_ok"] else 1)' \
  "$WORK/inputs-audit.json" || INPUTS_RC=1
gate inputs_audit "$INPUTS_RC"
[ "$INPUTS_RC" -eq 0 ] || { note 'input floor unknown/higher than 15.0: approval stopped'; exit 1; }

# --------------------------------------------------- effective settings -----
note '=== effective build settings ==='
"$DEVELOPER_DIR/usr/bin/xcodebuild" \
  -project "$WORK/src/native/RealTimeCutVADCXXLibrary.xcodeproj" \
  -scheme RealTimeCutVADCXXLibrary -configuration Release \
  -showBuildSettings 2>"$WORK/settings-raw.log" \
  | grep -E '^\s+(IPHONEOS_DEPLOYMENT_TARGET|SDKROOT|SUPPORTED_PLATFORMS|ARCHS|PRODUCT_NAME)' \
  > "$WORK/settings.txt" || true
cat "$WORK/settings.txt"

# -------------------------------------------------------------- build -------
note '=== xcodebuild: device arm64, explicit 15.0, signing off ==='
mkdir -p "$WORK/out"
"$DEVELOPER_DIR/usr/bin/xcodebuild" \
  -project "$WORK/src/native/RealTimeCutVADCXXLibrary.xcodeproj" \
  -scheme RealTimeCutVADCXXLibrary \
  -configuration Release \
  -sdk iphoneos \
  -destination 'generic/platform=iOS' \
  -derivedDataPath "$WORK/out/DerivedData" \
  ARCHS=arm64 ONLY_ACTIVE_ARCH=NO \
  IPHONEOS_DEPLOYMENT_TARGET=15.0 \
  CODE_SIGNING_ALLOWED=NO CODE_SIGN_IDENTITY=- CODE_SIGNING_REQUIRED=NO \
  build > "$WORK/out/build.log" 2>&1
BUILD_RC=$?
note "build rc=$BUILD_RC (log $(wc -c < "$WORK/out/build.log") bytes)"
gate build "$BUILD_RC"

PRODUCTS="$WORK/out/DerivedData/Build/Products/Release-iphoneos"

# ------------------------------------------------------- audit output -------
note '=== audit produced framework ==='
FRAMEWORK="$(find "$PRODUCTS" -maxdepth 1 -name 'RealTimeCutVADCXXLibrary.framework' -print -quit)"
OUTPUT_RC=1
if [ -n "$FRAMEWORK" ] && [ "$BUILD_RC" -eq 0 ]; then
  BINARY="$FRAMEWORK/RealTimeCutVADCXXLibrary"
  PLIST="$FRAMEWORK/Info.plist"
  file "$BINARY" > "$WORK/out/output-file.txt"
  otool -l "$BINARY" > "$WORK/out/output-otool-l.txt" 2>&1
  otool -Iv "$BINARY" > "$WORK/out/output-otool-Iv.txt" 2>&1
  otool -L "$BINARY" > "$WORK/out/output-otool-L.txt" 2>&1
  sha256_file "$BINARY" > "$WORK/out/output-binary.sha256"
  sha256_file "$PLIST" > "$WORK/out/output-plist.sha256"
  PROBE_SCRIPTS="$(pwd)/scripts" python3 - "$WORK/out" "$FRAMEWORK" \
    > "$WORK/out/output-audit.json" <<'PY'
import json, os, plistlib, sys
from pathlib import Path
out = Path(sys.argv[1]); framework = Path(sys.argv[2])
sys.path.insert(0, os.environ['PROBE_SCRIPTS'])
from audit_ios15_native_floor import audit_dependencies, audit_image
binary = framework / 'RealTimeCutVADCXXLibrary'
plist = plistlib.loads((framework / 'Info.plist').read_bytes())
audit = audit_image(
    (out / 'output-otool-l.txt').read_text(errors='replace'),
    (out / 'output-file.txt').read_text(errors='replace'),
    str(binary))
audit['plist'] = plist
plist_min = plist.get('MinimumOSVersion')
plist_minimum_ok = plist_min is not None
if plist_min is not None:
    parts = [int(part) for part in str(plist_min).split('.')]
    plist_minimum_ok = (parts[0], parts[1] if len(parts) > 1 else 0) <= (15, 0)
audit['plist_minimum_ok'] = plist_minimum_ok
audit['exports'] = audit_dependencies(
    (out / 'output-otool-Iv.txt').read_text(errors='replace'))
print(json.dumps(audit, indent=1))
PY
  OUTPUT_RC=0
  python3 -c 'import json,sys
d=json.load(open(sys.argv[1]))
ok = d["plist_minimum_ok"] and not d["errors"] and any("set_vad_callback" in l for l in d["exports"]["export_lines"])
sys.exit(0 if ok else 1)' "$WORK/out/output-audit.json" || OUTPUT_RC=1
else
  printf '{"error": "no framework produced"}' > "$WORK/out/output-audit.json"
fi
gate output_audit "$OUTPUT_RC"

# ------------------------------------------- wrapper ABI compile (no link) --
note '=== compile real upstream wrapper against rebuilt framework ==='
WRAPPER_SRC="$WORK/src/wrapper/RealTimeCutVADLibrary/src"
mkdir -p "$WORK/wrapper-build"
SDK_PATH="$(xcrun --sdk iphoneos --show-sdk-path)"
"$DEVELOPER_DIR/usr/bin/clang" -x objective-c \
  -target arm64-apple-ios15.0 \
  -isysroot "$SDK_PATH" \
  -fobjc-arc -fmodules \
  -F "$PRODUCTS" \
  -I "$WRAPPER_SRC/include" \
  -c "$WRAPPER_SRC/VADWrapper.m" \
  -o "$WORK/wrapper-build/VADWrapper.o" > "$WORK/wrapper-build/compile.log" 2>&1
WRAPPER_RC=$?
note "wrapper compile rc=$WRAPPER_RC"
if [ "$WRAPPER_RC" -eq 0 ]; then
  sha256_file "$WORK/wrapper-build/VADWrapper.o" > "$WORK/wrapper-build/VADWrapper.o.sha256"
fi
gate wrapper_abi "$WRAPPER_RC"

note '=== probe complete ==='

# Overall verdict: nonzero if ANY gate failed (build success alone is not green).
OVERALL_RC=0
awk -F= '{ if ($2 != 0) rc = 1 } END { exit rc }' "$WORK/status.txt" || OVERALL_RC=$?
exit "$OVERALL_RC"