#!/usr/bin/env bash
# Build RealTimeCutVADCXXLibrary.framework from pinned C++ source at the
# iOS 15.0 deployment floor, for the app's local SPM package
# (vendor/RealTimeCutVADLibrary). This replaces the upstream prebuilt zip
# whose Mach-O minimum is 15.6 and would be refused by dyld on iOS 15.0-15.5.
#
# The probe workflow (ios15-vad-probe.yml) validates inputs/output with nine
# gates; this script is the production counterpart that actually produces the
# artifact the app links. Same pins, same toolchain, same override
# (IPHONEOS_DEPLOYMENT_TARGET=15.0, device arm64, signing off). It writes
# ONLY to $RUNNER_TEMP and vendor/RealTimeCutVADLibrary/Frameworks, and is
# invoked by the native-deps step BEFORE xcodebuild resolves packages.
set -euo pipefail

case "$(uname -s)" in
  Darwin) ;;
  *) printf 'error: build_vad_framework.sh requires macOS (uname=%s)\n' "$(uname -s)" >&2; exit 2 ;;
esac

export DEVELOPER_DIR="${DEVELOPER_DIR:?}"
NATIVE_SHA="${NATIVE_CXX_SHA:?}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENDOR_FRAMEWORKS="$REPO_ROOT/vendor/RealTimeCutVADLibrary/Frameworks"
WORK="${RUNNER_TEMP:-$TMPDIR}/vad-framework-build"
rm -rf "$WORK"; mkdir -p "$WORK/src" "$WORK/inputs" "$WORK/out" "$VENDOR_FRAMEWORKS"
export GIT_TERMINAL_PROMPT=0

echo "==> clone pinned C++ source $NATIVE_SHA"
git clone --quiet --filter=blob:none --no-checkout \
  https://github.com/helloooideeeeea/RealTimeCutVADCXXLibrary.git "$WORK/src/native"
git -C "$WORK/src/native" fetch --quiet --depth 1 origin "$NATIVE_SHA"
git -C "$WORK/src/native" checkout --quiet "$NATIVE_SHA"
test "$(git -C "$WORK/src/native" rev-parse HEAD)" = "$NATIVE_SHA"

# The native Xcode project references the input XCFrameworks at the
# repo-relative Frameworks/ path (upstream README instructs exactly this).
mkdir -p "$WORK/src/native/Frameworks"

echo "==> download and verify pinned native inputs"
sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}'
  else shasum -a 256 "$1" | awk '{print $1}'; fi
}
curl -fsSL --retry 2 --max-time 900 -o "$WORK/inputs/onnxruntime.xcframework.zip" "${ONNX_URL:?}"
curl -fsSL --retry 2 --max-time 900 -o "$WORK/inputs/webrtc_audio_processing.xcframework.zip" "${APM_URL:?}"
ONNX_SHA="$(sha256_file "$WORK/inputs/onnxruntime.xcframework.zip")"
APM_SHA="$(sha256_file "$WORK/inputs/webrtc_audio_processing.xcframework.zip")"
test "$ONNX_SHA" = "${ONNX_SHA256:?}" || { echo "error: onnxruntime sha mismatch $ONNX_SHA" >&2; exit 1; }
test "$APM_SHA" = "${APM_SHA256:?}" || { echo "error: webrtc_apm sha mismatch $APM_SHA" >&2; exit 1; }
python3 - "$WORK/inputs" "$WORK/src/native/Frameworks" <<'PY'
import sys, zipfile
from pathlib import Path
src = Path(sys.argv[1]); dest = Path(sys.argv[2])
for archive in ('onnxruntime.xcframework.zip', 'webrtc_audio_processing.xcframework.zip'):
    with zipfile.ZipFile(src / archive) as zipped:
        zipped.extractall(dest)
        print('extracted %s -> %s' % (archive, dest))
PY

echo "==> xcodebuild: device arm64, explicit 15.0, signing off"
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
  build > "$WORK/build.log" 2>&1
grep -q '** BUILD SUCCEEDED **' "$WORK/build.log" || { tail -40 "$WORK/build.log"; echo 'error: VAD build failed' >&2; exit 1; }

FRAMEWORK="$WORK/out/DerivedData/Build/Products/Release-iphoneos/RealTimeCutVADCXXLibrary.framework"
test -d "$FRAMEWORK" || { echo 'error: built framework missing' >&2; exit 1; }

echo "==> install local framework"
rm -rf "$VENDOR_FRAMEWORKS/RealTimeCutVADCXXLibrary.framework"
cp -R "$FRAMEWORK" "$VENDOR_FRAMEWORKS/RealTimeCutVADCXXLibrary.framework"

BIN="$VENDOR_FRAMEWORKS/RealTimeCutVADCXXLibrary.framework/RealTimeCutVADCXXLibrary"
test -f "$BIN"
echo "==> output binary: $BIN"
file "$BIN"
otool -l "$BIN" | grep -A 3 LC_BUILD_VERSION | head -5
printf 'sha256: '; sha256_file "$BIN"
echo "==> done"