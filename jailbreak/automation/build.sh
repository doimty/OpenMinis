#!/bin/bash
# Actions-only native build. No device installation or service activation.
set -euo pipefail
scheme="${1:?rootless or roothide required}"
output="${2:?absolute fresh output directory required}"
case "$scheme" in rootless|roothide) ;; *) exit 2 ;; esac
case "$output" in /*) ;; *) printf 'Output directory must be absolute\n' >&2; exit 2 ;; esac
test "$(uname -s)" = Darwin
task_root="$(cd -- "$(dirname -- "$0")" && pwd)"
test -n "${GITHUB_SHA:-}" -a -n "${GITHUB_RUN_ID:-}"
mkdir -p "$output"
test -z "$(ls -A "$output")"
exec > >(tee "$output/build.log") 2>&1
work="$(mktemp -d "${RUNNER_TEMP:-/tmp}/minis-automation.XXXXXX")"
trap 'rm -rf -- "$work"' EXIT
python3 "$task_root/prepare_sources.py" --output "$work/source" --lock "$output/source-lock.json"
mkdir -p "$work/include"
if [ "$scheme" = roothide ]; then
  cp "$task_root/vendor/libroothide/roothide.h" "$work/include/roothide.h"
else
  cp "$task_root/vendor/libroothide/stub.h" "$work/include/roothide.h"
fi
# Link-only stub derived from the pinned official public C ABI and install name.
# The runtime implementation is the device's roothide core package, never bundled.
cat > "$work/include/libroothide.tbd" <<'TBD'
--- !tapi-tbd-v3
archs: [ arm64, arm64e ]
platform: ios
install-name: '@loader_path/.jbroot/usr/lib/libroothide.dylib'
current-version: 1
compatibility-version: 1
exports:
  - archs: [ arm64, arm64e ]
    symbols: [ _jbroot, _rootfs ]
...
TBD
sdk="$(xcrun --sdk iphoneos --show-sdk-path)"
for arch in arm64 arm64e; do
  mkdir -p "$work/$arch"
  flags=(-target "${arch}-apple-ios15.0" -isysroot "$sdk" -fobjc-arc -fblocks -fvisibility=hidden
    -O2 -Wall -Wextra -Wno-unused-function -Wno-deprecated-declarations -Wno-nullability-completeness
    -I "$work/source" -I "$work/include"
    "-DOMMA_BUILD_COMMIT=\"$GITHUB_SHA\"" "-DOMMA_BUILD_RUN_ID=\"$GITHUB_RUN_ID\"")
  if [ "$scheme" = roothide ]; then flags+=(-DMCP_ROOTHIDE=1); else flags+=(-DMCP_ROOTLESS=1); fi
  for source in "$work/source/"*.m; do
    xcrun --sdk iphoneos clang "${flags[@]}" -c "$source" -o "$work/$arch/$(basename "${source%.m}").o"
  done
  linkargs=(-target "${arch}-apple-ios15.0" -isysroot "$sdk" -dynamiclib)
  if [ "$scheme" = roothide ]; then linkargs+=(-L "$work/include" -lroothide); fi
  xcrun --sdk iphoneos clang "${linkargs[@]}" \
    -Wl,-install_name,@rpath/MinisAutomation.dylib -Wl,-undefined,dynamic_lookup \
    -framework Foundation -framework CoreFoundation -framework IOKit -framework UIKit \
    -framework CoreGraphics -framework QuartzCore -framework MobileCoreServices \
    -framework AVFoundation -framework Security -framework Vision -framework ImageIO \
    "$work/$arch/"*.o -o "$work/$arch/MinisAutomation.dylib"
done
xcrun lipo -create "$work/arm64/MinisAutomation.dylib" "$work/arm64e/MinisAutomation.dylib" \
  -output "$output/MinisAutomation.dylib"
codesign --force --sign - --timestamp=none "$output/MinisAutomation.dylib"
codesign --verify --strict "$output/MinisAutomation.dylib"
cp "$task_root/MinisAutomation.plist" "$output/MinisAutomation.plist"
cp "$task_root/vendor/ios-mcp/LICENSE" "$output/LICENSE.ios-mcp"
cp "$task_root/vendor/ios-mcp/NOTICE" "$output/NOTICE.ios-mcp"
cp "$task_root/vendor/ios-mcp/THIRD_PARTY_NOTICES.md" "$output/THIRD_PARTY_NOTICES.ios-mcp.md"
cp "$task_root/vendor/libroothide/LICENSE" "$output/LICENSE.RootHide"
chmod 0755 "$output/MinisAutomation.dylib"
python3 "$task_root/verify_build.py" --folder "$output" --scheme "$scheme"
