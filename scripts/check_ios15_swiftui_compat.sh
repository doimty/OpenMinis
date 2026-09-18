#!/usr/bin/env bash
# Run with the same DEVELOPER_DIR as the full pinned Xcode build.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
sdk="$(xcrun --sdk iphoneos --show-sdk-path)"
work="$(mktemp -d "${TMPDIR:-/tmp}/openminis-ios15-ui.XXXXXX")"
trap 'rm -rf "$work"' EXIT
fixture="$root/scripts/ios15-smoke"
shared="$root/src/ios/Shared"
production=(
  "$shared/SwiftUICompatibility.swift"
  "$shared/CompatNavigationPath.swift"
  "$shared/CompatPhotoPicker.swift"
  "$shared/CompatGeometry.swift"
  "$shared/LegacyHostingContent.swift"
  "$shared/LegacyFlowLayout.swift"
)

check() {
  local minimum="$1"
  shift
  xcrun --sdk iphoneos swiftc -typecheck -parse-as-library \
    -swift-version 5 -target "arm64-apple-ios${minimum}" -sdk "$sdk" \
    -module-cache-path "$work/ModuleCache" "$@"
}

# First rule out invalid syntax or a broken SDK in the negative control.
check 16.0 "$fixture/NativeOnly.swift"
if check 15.0 "$fixture/NativeOnly.swift" > "$work/native-ios15.log" 2>&1; then
  echo 'FAIL: native iOS 16 controls unexpectedly type-checked at iOS 15' >&2
  exit 1
fi
for symbol in LabeledContent NavigationStack presentationDetents persistentSystemOverlays UnevenRoundedRectangle contextMenu isElementFullscreenEnabled addsPunctuation sleep milliseconds; do
  if ! grep -E "error: '.*${symbol}.*' is only available in iOS" "$work/native-ios15.log" >/dev/null; then
    cat "$work/native-ios15.log" >&2
    echo "FAIL: negative control did not diagnose $symbol availability" >&2
    exit 1
  fi
done
echo 'PASS: native API negative control fails at 15 and passes at 16'
cat "$work/native-ios15.log"

failed=0
for minimum in 15.0 16.0; do
  if check "$minimum" "${production[@]}" "$fixture/CompatibilityCalls.swift"; then
    echo "PASS: all six production compatibility modules type-check at iOS $minimum"
  else
    echo "FAIL: production compatibility type-check at iOS $minimum" >&2
    failed=1
  fi
done
exit "$failed"
