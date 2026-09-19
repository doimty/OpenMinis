#!/usr/bin/env bash
# Run under the same pinned Xcode as the application build.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/openminis-symbols.XXXXXX")"
trap 'rm -rf "$work"' EXIT
helper="$root/src/ios/Shared/CompatSystemSymbol.swift"
fixture="$root/scripts/ios15-smoke"
ios_sdk="$(xcrun --sdk iphoneos --show-sdk-path)"
for minimum in 15.0 16.0; do
  xcrun --sdk iphoneos swiftc -typecheck -parse-as-library -swift-version 5 \
    -target "arm64-apple-ios${minimum}" -sdk "$ios_sdk" \
    -module-cache-path "$work/ModuleCache" \
    "$helper" "$fixture/SFSymbolCalls.swift"
  echo "PASS: production symbol resolver + native Image/Label/UIImage call-shapes at iOS $minimum"
done
host_sdk="$(xcrun --sdk macosx --show-sdk-path)"
xcrun --sdk macosx swiftc -parse-as-library -swift-version 5 \
  -target "$(uname -m)-apple-macosx13.0" -sdk "$host_sdk" \
  -module-cache-path "$work/HostModuleCache" \
  "$helper" "$fixture/SFSymbolFallbackTests.swift" -o "$work/symbol-tests"
"$work/symbol-tests" "$root/scripts/fixtures/sf-symbols-availability.json"
