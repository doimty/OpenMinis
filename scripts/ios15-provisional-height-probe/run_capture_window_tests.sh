#!/usr/bin/env bash
# Foundation collector control tests only; not a UIKit rendering oracle.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="${1:?collector evidence directory required}"
test -f "$OUT/Recorder.swift"
python3 - "$ROOT" "$OUT" <<'PY'
import importlib.util,sys
from pathlib import Path
root,out=map(Path,sys.argv[1:])
spec=importlib.util.spec_from_file_location('prepare',root/'scripts/ios15-provisional-height-probe/prepare_device_probe.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
base=(out/'Recorder.swift').read_text()
bridge=m.READONLY_COLLECTOR
(out/'RecorderWindow.swift').write_text(base+'\n'+bridge)
# This mutant still compiles; the native assertion, not a compiler failure,
# must reject a pause implementation that lets records through.
old='''    func nativeProbePauseCapture() {
        lock.lock()
        closed = true
        lock.unlock()
    }'''
assert old in bridge
bad=bridge.replace(old,old.replace('closed = true','closed = false'))
(out/'RecorderWindow-noop.swift').write_text(base+'\n'+bad)
# Extract only the diagnostic driver's two scalar callbacks. No renderer,
# layout, host, cell or ViewModel source is sliced or substituted here.
source=(root/'scripts/ios15-provisional-height-probe/DeviceProbe.swift').read_text()
def method(start, following):
    assert source.count(start)==1 and source.count(following)==1
    begin=source.index(start)
    return source[begin:source.index(following,begin)].replace('@objc ', '')
marker=method('    private func marker(_ phase: String', '    private func rect(_ r: CGRect) -> [Double] {\n        [Double(r.minX), Double(r.minY), Double(r.width), Double(r.height)]\n    }\n\n    // Length-prefixed')
assert marker.count('ReentryDiagnostics.shared')==1
marker=marker.replace('ReentryDiagnostics.shared','recorder')
pan=method('    @objc private func panObserved(', '    @objc private func reenter()')
prefix='''import Foundation
final class UIPanGestureRecognizer {
    enum State: Int { case began = 1, ended = 3, cancelled = 4 }
    let state: State
    init(_ state: State) { self.state = state }
}
final class NativeGestureDriverHarness {
    let recorder: ReentryDiagnostics
    let collection = NSObject()
    var captureActive = true
    var gestureBegins = 0
    var gestureEnds = 0
    init(recorder: ReentryDiagnostics) { self.recorder = recorder }
    func send(_ state: UIPanGestureRecognizer.State) { panObserved(UIPanGestureRecognizer(state)) }
'''
(out/'GestureDriver.swift').write_text(prefix+marker+pan+'}\n')
mutant=pan
for counter in ('gestureBegins','gestureEnds'):
    line='                "gesture": Double('+counter+')\n'
    assert mutant.count(line)==1
    mutant=mutant.replace(line,'')
(out/'GestureDriver-noordinal.swift').write_text(prefix+marker+mutant+'}\n')
PY
SDK="$(xcrun --sdk macosx --show-sdk-path)"
compile() {
  xcrun --sdk macosx swiftc -swift-version 6 -D DEBUG -parse-as-library \
    -target "$(uname -m)-apple-macosx13.0" -sdk "$SDK" \
    "$1" "$3" "$ROOT/scripts/ios15-provisional-height-probe/CaptureWindowTests.swift" -o "$2"
}
compile "$OUT/RecorderWindow.swift" "$OUT/window-tests" "$OUT/GestureDriver.swift" > "$OUT/window-compile.log" 2>&1 || { cat "$OUT/window-compile.log"; exit 1; }
"$OUT/window-tests" | tee "$OUT/window-tests.log"
compile "$OUT/RecorderWindow-noop.swift" "$OUT/window-noop" "$OUT/GestureDriver.swift" > "$OUT/window-noop-compile.log" 2>&1 || { cat "$OUT/window-noop-compile.log"; exit 1; }
set +e
"$OUT/window-noop" > "$OUT/window-noop.log" 2>&1
status=$?
set -e
test "$status" -eq 1
grep -Fq 'FAIL: paused collector must not record' "$OUT/window-noop.log"
printf '%s\n' 'PASS: compiled no-op pause rejected by native behavioral assertion' | tee -a "$OUT/window-tests.log"
compile "$OUT/RecorderWindow.swift" "$OUT/window-noordinal" "$OUT/GestureDriver-noordinal.swift" > "$OUT/window-noordinal-compile.log" 2>&1 || { cat "$OUT/window-noordinal-compile.log"; exit 1; }
set +e
"$OUT/window-noordinal" > "$OUT/window-noordinal.log" 2>&1
status=$?
set -e
test "$status" -eq 1
grep -Fq 'FAIL: repeated physical-state callbacks retain every marker' "$OUT/window-noordinal.log"
printf '%s\n' 'PASS: compiled missing-ordinal callback mutation rejected by native behavioral assertion' | tee -a "$OUT/window-tests.log"
