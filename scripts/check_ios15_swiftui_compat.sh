#!/usr/bin/env bash
# Run with the same DEVELOPER_DIR as the full pinned Xcode build.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
sdk="$(xcrun --sdk iphoneos --show-sdk-path)"
work="$(mktemp -d "${TMPDIR:-/tmp}/openminis-ios15-ui.XXXXXX")"
trap 'rm -rf "$work"' EXIT
fixture="$root/scripts/ios15-smoke"
compat="$root/src/ios/Shared/SwiftUICompatibility.swift"

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
for symbol in LabeledContent NavigationStack presentationDetents; do
  if ! grep -F "error: '$symbol' is only available in iOS 16.0 or newer" "$work/native-ios15.log" >/dev/null; then
    cat "$work/native-ios15.log" >&2
    echo "FAIL: negative control did not diagnose $symbol availability" >&2
    exit 1
  fi
done
echo 'PASS: native API negative control fails at 15 and passes at 16'

for minimum in 15.0 16.0; do
  check "$minimum" "$compat" "$fixture/CompatibilityCalls.swift"
  echo "PASS: production SwiftUI compatibility calls type-check at iOS $minimum"
done
