#!/usr/bin/env bash
# Native Foundation collector tests, not a UIKit rendering/re-entry oracle.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:?output directory required}"
mkdir -p "$OUT"
python3 "$ROOT/scripts/reentry-diagnostics/check_source.py" --output "$OUT"
test "$(xcodebuild -version | sed -n '1p')" = 'Xcode 26.2'
test "$(xcodebuild -version | sed -n '2p')" = 'Build version 17C52'
SDK="$(xcrun --sdk macosx --show-sdk-path)"
TESTS="$ROOT/scripts/reentry-diagnostics/CollectorTests.swift"
compile() {
    xcrun --sdk macosx swiftc -swift-version 6 -D DEBUG -parse-as-library \
        -target "$(uname -m)-apple-macosx13.0" -sdk "$SDK" \
        "$1" "$TESTS" -o "$2"
}
compile "$OUT/Recorder.swift" "$OUT/collector-tests" > "$OUT/compile.log" 2>&1 \
    || { cat "$OUT/compile.log"; exit 1; }
"$OUT/collector-tests" > "$OUT/native-tests.log" 2>&1 \
    || { cat "$OUT/native-tests.log"; exit 1; }
cat "$OUT/native-tests.log"
# Prove this test actually rejects a silent/no-op collector. Compilation
# must succeed first; a compiler error is INVALID, not an expected red test.
compile "$OUT/Recorder-noop.swift" "$OUT/collector-noop" > "$OUT/noop-compile.log" 2>&1 \
    || { cat "$OUT/noop-compile.log"; exit 1; }
set +e
"$OUT/collector-noop" > "$OUT/noop-tests.log" 2>&1
status=$?
set -e
test "$status" -eq 1
grep -Fq 'FAIL: enabled recorder emits and deduplicates' "$OUT/noop-tests.log"
printf '%s\n' 'PASS: no-op collector rejected by native behavioral assertion' | tee -a "$OUT/native-tests.log"
# Syntax-check actual observation sites with DEBUG on. Full type/link check
# follows in the device App build; neither step is called a UI test.
IOS_SDK="$(xcrun --sdk iphoneos --show-sdk-path)"
python3 - "$ROOT" "$OUT" <<'PY'
import sys
from pathlib import Path
sys.path.insert(0,str(Path(sys.argv[1])/'scripts/reentry-diagnostics'))
from check_source import SOURCES
(Path(sys.argv[2])/'source-paths.txt').write_text('\n'.join(SOURCES)+'\n')
PY
while IFS= read -r source; do
    xcrun --sdk iphoneos swiftc -frontend -parse -D DEBUG -enable-bare-slash-regex \
        -target arm64-apple-ios15.0 -sdk "$IOS_SDK" "$ROOT/$source"
done < "$OUT/source-paths.txt"
