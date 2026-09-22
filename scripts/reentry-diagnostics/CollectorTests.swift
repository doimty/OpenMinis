import Foundation

// Non-rendering logging adapter. Tests inject their own sink into the EXACT
// production collector extracted from AppLogger.swift; no UI is simulated here.
struct AppLogger {
    init(category: String) {}
    func info(_ message: String) {}
}

final class Lines: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String] = []
    func add(_ value: String) {
        lock.lock(); defer { lock.unlock() }
        storage.append(value)
    }
    func get() -> [String] {
        lock.lock(); defer { lock.unlock() }
        return storage
    }
}

func require(_ condition: Bool, _ label: String) {
    if !condition {
        FileHandle.standardError.write(Data(("FAIL: " + label + "\n").utf8))
        exit(1)
    }
}

func events(_ lines: Lines) throws -> [[String: Any]] {
    try lines.get().map { line in
        guard let result = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else {
            throw CocoaError(.coderReadCorrupt)
        }
        return result
    }
}

@main
struct CollectorTests {
    static func main() throws {
        let sha = String(repeating: "a", count: 40)
        let disabledLines = Lines()
        let disabled = ReentryDiagnostics(enabled: false, commit: sha, output: { disabledLines.add($0) })
        disabled.record(kind: "mount", values: ["height": 100])
        disabled.drainForTesting()
        require(disabledLines.get().isEmpty, "disabled recorder stays silent")
        require(!disabled.canRecord, "disabled recorder rejects capture")

        let lines = Lines()
        let recorder = ReentryDiagnostics(enabled: true, commit: sha, output: { lines.add($0) })
        recorder.record(kind: "text-size", phase: "finite", values: ["height": 100])
        recorder.record(kind: "text-size", phase: "finite", values: ["height": 100])
        recorder.record(kind: "text-size", phase: "finite", values: ["height": 180])
        recorder.drainForTesting()
        let rows = try events(lines)
        // The native no-op mutation MUST fail here, not merely fail to compile.
        require(rows.count == 2, "enabled recorder emits and deduplicates")
        require(rows[0]["commit"] as? String == sha, "provenance survives serialization")
        require(rows[0]["seq"] as? Int == 1 && rows[1]["seq"] as? Int == 2, "sequence is continuous")

        let cappedLines = Lines()
        let capped = ReentryDiagnostics(enabled: true, commit: sha, limit: 2, output: { cappedLines.add($0) })
        for i in 0..<20 { capped.record(kind: "viewport", values: ["offset": Double(i)]) }
        capped.drainForTesting()
        let capRows = try events(cappedLines)
        require(capRows.count == 3, "event budget is bounded including one limit marker")
        require(capRows.last?["kind"] as? String == "limit", "truncation is explicit")
        require(!capped.canRecord, "cap disables further capture work")

        let badLines = Lines()
        let bad = ReentryDiagnostics(enabled: true, commit: "not-a-sha", output: { badLines.add($0) })
        bad.record(kind: "not a safe label", session: "private text should not escape",
                   values: ["height": .nan, "invalid key": 2, "second invalid key": 3])
        bad.drainForTesting()
        let badRows = try events(badLines)
        require(badRows.count == 1, "nonfinite input never drops the event")
        require(badRows[0]["commit"] as? String == "unknown", "invalid provenance is not invented")
        require(badRows[0]["session"] as? String == "none", "non-UUID session text excluded")
        let numbers = badRows[0]["values"] as? [String: Any]
        require(numbers?["height"] as? String == "nonfinite", "nonfinite is explicit, not zero")
        require(numbers?.count == 1, "invalid field labels omitted without key collisions")
        require(!badLines.get().joined().contains("private text"), "no arbitrary text in telemetry")

        let concurrentLines = Lines()
        let concurrent = ReentryDiagnostics(enabled: true, commit: sha, output: { concurrentLines.add($0) })
        DispatchQueue.concurrentPerform(iterations: 80) { index in
            concurrent.record(kind: "viewport", index: index, values: ["offset": Double(index)])
        }
        concurrent.drainForTesting()
        let concurrentRows = try events(concurrentLines)
        require(concurrentRows.count == 80, "concurrent producers retain all distinct events")
        require(concurrentRows.compactMap { $0["seq"] as? Int } == Array(1...80), "queue order matches sequence under concurrency")

        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: directory) }
        let fileLines = Lines()
        let file = ReentryDiagnostics(enabled: true, commit: sha, directory: directory, output: { fileLines.add($0) })
        file.record(kind: "mount", values: ["width": 428])
        file.record(kind: "unmount")
        file.drainForTesting()
        let urls = try FileManager.default.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil)
        require(urls.count == 1 && urls[0].pathExtension == "log", "dedicated log is visible to existing log browser")
        let text = try String(contentsOf: urls[0], encoding: .utf8)
        require(text.split(separator: "\n").count == 2, "dedicated log preserves all events")
        require(text.contains("[ReentryTrace] [INFO] [REENTRYDIAG]"), "dedicated log uses the parser category prefix")
        print("PASS: real collector disable/dedup/budget/privacy/nonfinite/concurrency/file-output checks")
    }
}
