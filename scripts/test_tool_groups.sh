#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "SKIP: tool-group projection regression needs macOS swiftc; Linux exit 77 is not a native pass."
  exit 77
fi
if [[ -n "${SWIFTC:-}" ]]; then
  compiler="$SWIFTC"
else
  compiler="$(xcrun --find swiftc 2>/dev/null || command -v swiftc || true)"
fi
if [[ -z "$compiler" || ! -x "$compiler" ]]; then
  echo "error: Swift compiler not found (set SWIFTC or install Xcode)" >&2
  exit 2
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
sdk_args=()
if command -v xcrun >/dev/null 2>&1 && [[ -z "${SWIFTC:-}" ]]; then
  sdk="$(xcrun --sdk macosx --show-sdk-path)"
  sdk_args=(-sdk "$sdk" -target "$(uname -m)-apple-macosx12.0")
fi

PRODUCTION="$ROOT/src/ios/Agent/MessageList/ToolGroupProjection.swift"
TESTS="$ROOT/scripts/tool-group-tests/main.swift"

echo "== baseline =="
"$compiler" -swift-version 5 "${sdk_args[@]}" "$PRODUCTION" "$TESTS" -o "$tmp/tool-group-tests"
"$tmp/tool-group-tests"

# Mutants must compile with the SAME SDK/target as the baseline. Without it the
# compiler defaults to the runner's own deployment target and every mutant dies
# on "unable to load standard library", which proves nothing about the tests.
mutant_args=()
for arg in "${sdk_args[@]}"; do
  mutant_args+=("--swift-arg=$arg")
done

echo "== mutants =="
python3 "$ROOT/scripts/tool-group-tests/mutants.py" \
  --swiftc "$compiler" \
  --source "$PRODUCTION" \
  --test "$TESTS" \
  --work "$tmp/mutants" \
  "${mutant_args[@]}"
