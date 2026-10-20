#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "SKIP: ChatSearchQuery regression requires macOS and native swiftc; Linux is not a native pass."
  exit 77
fi
if [[ -n "${SWIFTC:-}" ]]; then
  compiler="$SWIFTC"
elif command -v xcrun >/dev/null 2>&1; then
  compiler="$(xcrun --find swiftc)"
else
  compiler="$(command -v swiftc || true)"
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
  target="$(xcrun --sdk macosx --show-sdk-platform-path)/Developer/usr/bin/swiftc"
  if [[ -x "$target" ]]; then
    compiler="$target"
  fi
  sdk_args=(-sdk "$sdk" -target "$(uname -m)-apple-macosx12.0")
fi
"$compiler" -swift-version 5 "${sdk_args[@]}" \
  "$ROOT/src/ios/Agent/Chat/ChatSearchQuery.swift" \
  "$ROOT/scripts/chat-search-tests/main.swift" \
  -o "$tmp/chat-search-tests"
"$tmp/chat-search-tests"
