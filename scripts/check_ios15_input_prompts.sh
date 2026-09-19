#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
work="$(mktemp -d "${TMPDIR:-/tmp}/openminis-input.XXXXXX")"
trap 'rm -rf "$work"' EXIT
shared="$root/src/ios/Shared"
fixture="$root/scripts/ios15-smoke"
ios_sdk="$(xcrun --sdk iphoneos --show-sdk-path)"
for minimum in 15.0 16.0; do
  xcrun --sdk iphoneos swiftc -typecheck -parse-as-library -swift-version 5 \
    -target "arm64-apple-ios${minimum}" -sdk "$ios_sdk" \
    -module-cache-path "$work/ModuleCache" \
    "$shared/InputPromptLifecycle.swift" "$shared/CompatTextInputAlert.swift" \
    "$fixture/InputPromptCalls.swift"
  echo "PASS: input adapter call-shapes type-check at iOS $minimum"
done
host_sdk="$(xcrun --sdk macosx --show-sdk-path)"
xcrun --sdk macosx swiftc -parse-as-library -swift-version 5 \
  -target "$(uname -m)-apple-macosx13.0" -sdk "$host_sdk" \
  "$shared/InputPromptLifecycle.swift" "$fixture/InputPromptLifecycleTests.swift" \
  -o "$work/lifecycle-tests"
"$work/lifecycle-tests"
xcrun --sdk macosx swiftc -parse-as-library -swift-version 5 \
  -target "$(uname -m)-apple-macosx13.0" -sdk "$host_sdk" \
  "$shared/RequestReasoningDiagnostics.swift" "$fixture/RequestReasoningDiagnosticsTests.swift" \
  -o "$work/reasoning-diagnostic-tests"
"$work/reasoning-diagnostic-tests"
