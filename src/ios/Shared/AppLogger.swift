import Foundation

struct AppLogger {
    let category: String

    init(subsystem: String = "com.openminis.app", category: String) {
        self.category = category
    }

    // [T-ios-log-noise-reduction] DEBUG is suppressed in Release builds so
    // diagnostic chatter (agentHistory dumps, per-record sync traces, etc.)
    // downgraded to `.debug()` adds zero cost / zero noise to shipped logs,
    // while staying available to developers running a Debug build. Use
    // `@autoclosure` so the message string isn't even built in Release —
    // the interpolation cost is skipped entirely, not just the NSLog.
    func debug(_ message: @autoclosure () -> String) {
        #if DEBUG
        log("DEBUG", message())
        #endif
    }
    func info(_ message: String)     { log("INFO", message) }
    func notice(_ message: String)   { log("NOTICE", message) }
    func warning(_ message: String)  { log("WARN", message) }
    func error(_ message: String)    { log("ERROR", message) }
    func critical(_ message: String) { log("CRIT", message) }
    func fault(_ message: String)    { log("FAULT", message) }

    private func log(_ level: String, _ message: String) {
        NSLog("[%@] [%@] %@", category, level, message)
        if level == "INFO" || level == "WARN" || level == "ERROR" {
            let ts = Date().formatted(.dateTime.hour().minute().second())
            let line = "[\(ts)] [\(category)] \(message)"
            CrashReporter.shared.appendLog(line)
        }
    }
}

// REENTRY-DIAG-BEGIN
#if DEBUG
/// Diagnostic IPA only: scalar observations, never layout requests or UI writes.
/// The packager opts in through Info.plist; ordinary Debug/Release builds stay off.
final class ReentryDiagnostics: @unchecked Sendable {
    static let enabled = (Bundle.main.object(forInfoDictionaryKey: "MinisReentryDiagnostics") as? Bool) == true
    static let shared = ReentryDiagnostics(
        enabled: enabled,
        commit: (Bundle.main.object(forInfoDictionaryKey: "MinisDiagnosticCommit") as? String) ?? "unknown",
        directory: FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask).first?
            .appendingPathComponent("Logs", isDirectory: true),
        output: { AppLogger(category: "ReentryTrace").info("[REENTRYDIAG] " + $0) })
    static var active: Bool { enabled && shared.canRecord }

    static func identity(_ object: AnyObject?) -> String {
        object.map { String(ObjectIdentifier($0).hashValue, radix: 16) } ?? "none"
    }

    private let enabledForInstance: Bool
    private let commit: String
    private let runID: String
    private let logURL: URL?
    private let limit: Int
    private let output: @Sendable (String) -> Void
    private let lock = NSLock()
    private let queue = DispatchQueue(label: "com.openminis.reentry-diagnostics", qos: .utility)
    private var sequence = 0
    private var closed = false
    private var previous: [String: [String: Double]] = [:]
    // Accessed only by queue. File contains this trace alone, no message text.
    private var fileHandle: FileHandle?
    private var fileFailed = false

    init(enabled: Bool, commit: String, limit: Int = 8000, directory: URL? = nil,
         output: @escaping @Sendable (String) -> Void) {
        self.enabledForInstance = enabled
        let run = UUID().uuidString
        self.runID = run
        self.logURL = enabled ? directory?.appendingPathComponent("reentry-\(run).log") : nil
        self.commit = commit.count == 40 && commit.allSatisfy({ $0.isHexDigit })
            ? commit.lowercased() : "unknown"
        self.limit = max(1, limit)
        self.output = output
    }

    var canRecord: Bool {
        lock.lock()
        defer { lock.unlock() }
        return enabledForInstance && !closed
    }

    /// No arbitrary text payload: names are bounded labels, session is UUID-only,
    /// and measurements are numeric. Objects are reduced to process-local IDs.
    func record(kind: String, owner: AnyObject? = nil, subject: AnyObject? = nil,
                parent: AnyObject? = nil, index: Int = -1, generation: UInt = 0,
                phase: String = "", session: String? = nil,
                values: [String: Double] = [:]) {
        guard enabledForInstance else { return }
        let kind = Self.label(kind)
        let phase = Self.label(phase)
        let ownerID = Self.identity(owner)
        let subjectID = Self.identity(subject)
        let parentID = Self.identity(parent)
        let sessionID = session.flatMap(UUID.init(uuidString:)).map { String($0.uuidString.prefix(8)) } ?? "none"
        var numeric: [String: Double] = [:]
        for (key, value) in values.prefix(24) where Self.label(key) == key {
            numeric[key] = value
        }
        let measurementsToEncode = numeric
        let main = Thread.isMainThread
        let key = "\(kind)|\(ownerID)|\(subjectID)|\(parentID)|\(index)|\(generation)|\(phase)|\(sessionID)|\(main)"
        let time = ProcessInfo.processInfo.systemUptime
        lock.lock()
        defer { lock.unlock() }
        guard !closed else { return }
        if kind == "mount" { previous.removeAll(keepingCapacity: true) }
        if kind != "mount" && kind != "unmount", previous[key] == numeric { return }
        if previous.count >= 1024 { previous.removeAll(keepingCapacity: true) }
        previous[key] = numeric
        sequence += 1
        let seq = sequence
        let capped = seq > limit
        if capped { closed = true }
        let finalKind = capped ? "limit" : kind
        // Enqueue under the lock: concurrent producers keep sequence order.
        // JSON encoding and log I/O happen only on this bounded serial queue.
        queue.async { [self] in
            var measurements: [String: Any] = [:]
            for (key, value) in measurementsToEncode {
                measurements[key] = value.isFinite ? (value as Any) : "nonfinite"
            }
            let event: [String: Any] = [
                "schema": 1, "run": runID, "commit": commit, "seq": seq,
                "t": time, "main": main, "kind": finalKind,
                "owner": ownerID, "subject": subjectID, "parent": parentID,
                "idx": index, "generation": generation, "phase": phase,
                "session": sessionID, "values": measurements,
            ]
            do {
                let data = try JSONSerialization.data(withJSONObject: event, options: [.sortedKeys])
                if let line = String(data: data, encoding: .utf8) {
                    appendTraceFile(line)
                    output(line)
                }
            } catch {
                output("{\"schema\":1,\"kind\":\"encoding-error\"}")
            }
        }
    }

    private func appendTraceFile(_ line: String) {
        guard let url = logURL, !fileFailed else { return }
        do {
            if fileHandle == nil {
                try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
                guard FileManager.default.createFile(atPath: url.path, contents: nil) else {
                    throw CocoaError(.fileWriteUnknown)
                }
                fileHandle = try FileHandle(forWritingTo: url)
            }
            try fileHandle?.write(contentsOf: Data(("[ReentryTrace] [INFO] [REENTRYDIAG] " + line + "\n").utf8))
        } catch {
            fileFailed = true
            output("{\"schema\":1,\"kind\":\"io-error\"}")
        }
    }

    private static let allowedLabels = CharacterSet(charactersIn: "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-()")
    private static func label(_ value: String) -> String {
        guard !value.isEmpty, value.utf8.count <= 80,
              value.unicodeScalars.allSatisfy({ allowedLabels.contains($0) }) else { return "label" }
        return value
    }

    /// Native collector tests only. No production call site may drain the queue.
    func drainForTesting() { queue.sync {} }
}
#endif
// REENTRY-DIAG-END
