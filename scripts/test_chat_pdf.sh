#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ "$(uname -s)" != Darwin ]]; then
  echo "SKIP: ChatPDFText regression requires macOS and native swiftc; Linux is not a native pass."
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
"$compiler" -swift-version 5 "${sdk_args[@]}" \
  "$ROOT/src/ios/Views/Chat/ChatPDFText.swift" \
  "$ROOT/scripts/chat-pdf-tests/main.swift" \
  -o "$tmp/chat-pdf-tests"
"$tmp/chat-pdf-tests"
