import Foundation

@main
struct CaptureWindowTests {
    static func check(_ value: @autoclosure () -> Bool, _ name: String) {
        guard value() else { print("FAIL: \(name)"); exit(1) }
    }
    static func main() throws {
        let folder = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: folder) }
        let recorder = ReentryDiagnostics(enabled: true, commit: String(repeating: "a", count: 40), directory: folder, output: { _ in })
        let nonce = recorder.nativeProbeRunID
        recorder.nativeProbePauseCapture()
        check(!recorder.canRecord, "paused collector must not record")
        recorder.record(kind: "idle")
        try recorder.nativeProbeBeginCapture()
        recorder.record(kind: "started")
        recorder.nativeProbePauseCapture()
        recorder.record(kind: "after-finish")
        let events = try recorder.nativeProbeReadEvents()
        check(events.count == 1, "one-shot window excludes idle and finished events")
        check(events[0]["kind"] as? String == "started", "correct event retained")
        check(events[0]["seq"] as? Int == 1, "sequence starts at one without reset")
        check(events[0]["run"] as? String == nonce, "nonce remains unchanged")
        do { try recorder.nativeProbeBeginCapture(); check(false, "cannot reopen consumed window") }
        catch { }
        let capped = ReentryDiagnostics(enabled: true, commit: String(repeating: "a", count: 40), limit: 1, directory: folder, output: { _ in })
        capped.nativeProbePauseCapture()
        try capped.nativeProbeBeginCapture()
        capped.record(kind: "first")
        capped.record(kind: "second")
        let capEvents = try capped.nativeProbeReadEvents()
        check(capEvents.count == 2 && capEvents.last?["kind"] as? String == "limit", "original cap remains enforced")
        do { try capped.nativeProbeBeginCapture(); check(false, "cannot reopen capped window") }
        catch { }
        let gestures = ReentryDiagnostics(enabled: true, commit: String(repeating: "a", count: 40), directory: folder, output: { _ in })
        gestures.nativeProbePauseCapture()
        try gestures.nativeProbeBeginCapture()
        // This calls the exact extracted driver callbacks and actual collector.
        // Only UIPanGestureRecognizer's state is substituted. It is a collector
        // integration test, NOT proof of real UIKit gesture/render behavior.
        let driver = NativeGestureDriverHarness(recorder: gestures)
        driver.send(.began)
        driver.send(.ended)
        driver.send(.began)
        driver.send(.ended)
        driver.send(.began)
        driver.send(.cancelled)
        gestures.nativeProbePauseCapture()
        let gestureEvents = try gestures.nativeProbeReadEvents()
        check(gestureEvents.count == 6, "repeated physical-state callbacks retain every marker")
        check(driver.gestureBegins == 3 && driver.gestureEnds == 3, "all driver action counts retained")
        for (index, event) in gestureEvents.enumerated() {
            let values = event["values"] as? [String: Any]
            check(values?["gesture"] as? Int == index / 2 + 1, "gesture ordinals follow actual callbacks")
            check(event["phase"] as? String == (index % 2 == 0 ? "finger-began" : "finger-ended"), "gesture phases preserved")
        }
        print("PASS: actual collector one-shot pause/start/finish/cap behavior")
        print("PASS: actual driver callbacks retain repeated gesture markers")
    }
}
