import Foundation

struct AppLogger {
    let category: String

    init(subsystem: String = "com.openminis.app", category: String) {
        self.category = category
    }

    // MARK: - Log level

    /// [T-ios-log-verbose-tier] How much detail reaches the log file.
    ///
    /// Three tiers with three DIFFERENT lifetimes — the distinction that
    /// matters is not "how chatty" but "who is it for and when does it exist":
    ///
    ///   * `.debug`   — purely local debugging. Compiled OUT of Release
    ///                  entirely (`#if DEBUG`), so it can never reach a user's
    ///                  device no matter what the runtime setting says.
    ///   * `.verbose` — high-frequency traces that ARE wanted on a real device
    ///                  when chasing something (per-syscall fs traces, per-render
    ///                  tool lifecycle, per-request session traces). Present in
    ///                  Release but OFF by default; the user turns it on for a
    ///                  reproduction run and back off afterwards.
    ///   * `.info`    — the default. Ordinary operational record.
    ///
    /// Why this exists: two daily logs measured 775 MB and 171 MB. Six
    /// statements produced 73% and 99% of them respectively — all of them
    /// per-event traces that had been raised to `.info` for one investigation
    /// and never lowered again. One of them alone sustained 140 lines/second
    /// on an otherwise idle day, every line saying nothing had happened.
    /// `.debug` was not a usable home for them (it vanishes in Release, which
    /// is exactly where the traces are needed), so they had nowhere to go but
    /// `.info`. `.verbose` is that missing home.
    enum Level: Int, Comparable, CaseIterable {
        case verbose = 0
        case info    = 1

        static func < (a: Level, b: Level) -> Bool { a.rawValue < b.rawValue }

        var displayName: String {
            switch self {
            case .verbose: return "Verbose"
            case .info:    return "Info"
            }
        }
    }

    private static let levelKey = "logging.level"

    /// The active threshold. A call is emitted when its own level is >= this.
    ///
    /// Read on every log call, so it is cached in memory rather than hitting
    /// UserDefaults each time — at 140 lines/second a defaults read per line
    /// is itself a cost worth avoiding.
    nonisolated(unsafe) private static var cachedLevel: Level = {
        // Absent key → .info. `object(forKey:)` rather than `integer(forKey:)`
        // because the latter returns 0 for "not set", and 0 is `.verbose` —
        // a missing key would silently enable the firehose.
        guard let raw = UserDefaults.standard.object(forKey: levelKey) as? Int,
              let lvl = Level(rawValue: raw) else { return .info }
        return lvl
    }()

    private static let levelLock = NSLock()

    static var level: Level {
        get {
            levelLock.lock(); defer { levelLock.unlock() }
            return cachedLevel
        }
        set {
            levelLock.lock()
            cachedLevel = newValue
            levelLock.unlock()
            UserDefaults.standard.set(newValue.rawValue, forKey: levelKey)
        }
    }

    /// True when `.verbose` output is currently being recorded.
    ///
    /// Exposed so a caller can skip EXPENSIVE work that exists only to build a
    /// verbose message (walking a history, formatting a table). `verbose(_:)`
    /// already avoids the string interpolation itself via `@autoclosure`; this
    /// is for the cases where the argument is not the only cost.
    static var isVerboseEnabled: Bool { level <= .verbose }

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
    /// High-frequency trace. Present in Release but only recorded while the
    /// log level is `.verbose` — see `Level`.
    ///
    /// `@autoclosure` for the same reason `debug` uses it: when the level is
    /// `.info` (the default) the message is never built, so an unrecorded
    /// verbose call costs one integer comparison rather than a string
    /// interpolation. That matters here more than anywhere else, since these
    /// are precisely the calls that fire hundreds of times a second.
    func verbose(_ message: @autoclosure () -> String) {
        guard Self.level <= .verbose else { return }
        log("VERBOSE", message())
    }

    func info(_ message: String)     { log("INFO", message) }
    func notice(_ message: String)   { log("NOTICE", message) }
    func warning(_ message: String)  { log("WARN", message) }
    func error(_ message: String)    { log("ERROR", message) }
    func critical(_ message: String) { log("CRIT", message) }
    func fault(_ message: String)    { log("FAULT", message) }

    private func log(_ level: String, _ message: String) {
        // [T-ios27-scene-create-watchdog] Inside a dispatch_once this call must
        // not be allowed to block — see `deferDuringCriticalInit`.
        if Self.deferDuringCriticalInit {
            Self.enqueueDeferred(category: category, level: level, message: message)
            return
        }
        emit(level, message)
    }

    private func emit(_ level: String, _ message: String) {
        NSLog("[%@] [%@] %@", category, level, message)
        // VERBOSE reaches the file too — it is gated at the call site by the
        // level check, so anything arriving here was explicitly asked for.
        if level == "VERBOSE" || level == "INFO" || level == "WARN" || level == "ERROR" {
            let ts = Date().formatted(.dateTime.hour().minute().second())
            let line = "[\(ts)] [\(category)] \(message)"
            CrashReporter.shared.appendLog(line)
        }
    }

    // MARK: - Deferred logging during one-time initialization
    //
    // [T-ios27-scene-create-watchdog] An iPhone 15 Pro on iOS 27.0 (24A437)
    // was SIGKILLed by the `scene-create` watchdog after exhausting the 10 s
    // wall-clock allowance, with only 0.161 s of application CPU — blocked,
    // not busy. The symbolicated deadlock:
    //
    //   thread 9 (background QoS)  holds the dispatch_once for ChatStore.shared
    //     ChatStore.init -> createTables -> iCloudLogger.info
    //       -> NSLog -> (iOS 27 libtrace) -> NSNotificationCenter post
    //         -> -[NSOperation waitUntilFinished]     ← parked here
    //   main thread                waits for that same once token
    //     ContentView.body -> ChatStore.shared -> _dispatch_once_wait
    //
    // On iOS 27 the os_log/NSLog state-request path can take a notification
    // round trip (thread 10 in the report sat in
    // `___os_state_request_for_self_block_invoke` inside a blocked
    // dispatch_sync). A log line that used to cost microseconds can therefore
    // stall arbitrarily — and any stall inside a dispatch_once is inherited by
    // every thread that later touches that singleton, including the main
    // thread during scene creation.
    //
    // The rule this enforces: code running inside a one-time initializer never
    // calls into the system logging stack. Lines are appended to a small
    // in-memory buffer and flushed once initialization has returned, so the
    // diagnostics are preserved without the lock inversion.

    // Deferral state is THREAD-LOCAL, not process-global.
    //
    // The hazard being guarded is "this thread is inside a dispatch_once and
    // must not touch the logging stack" — a property of one thread, never of
    // the process. A global flag would also silence every OTHER thread for the
    // duration, which reorders their log lines and, worse, loses them outright
    // if the app crashes inside the window. Crash-window logs are exactly the
    // ones an investigation needs. The 512-line cap is per-thread for the same
    // reason: one busy thread must not consume another's budget.
    private static let deferralDepthKey = "com.openminis.applogger.deferralState"

    private final class DeferralBox {
        var depth = 0
        var buffer: [(String, String, String)] = []
    }

    private static var deferralBox: DeferralBox? {
        Thread.current.threadDictionary[deferralDepthKey] as? DeferralBox
    }

    private static func deferralBoxCreating() -> DeferralBox {
        if let existing = deferralBox { return existing }
        let box = DeferralBox()
        Thread.current.threadDictionary[deferralDepthKey] = box
        return box
    }

    private static var deferDuringCriticalInit: Bool {
        (deferralBox?.depth ?? 0) > 0
    }

    private static func enqueueDeferred(category: String, level: String, message: String) {
        guard let box = deferralBox else { return }
        // Bounded: a runaway initializer must not turn a logging problem into a
        // memory problem. 512 lines is far more than any init emits.
        if box.buffer.count < 512 {
            box.buffer.append((category, level, message))
        }
    }

    /// Run `body` with logging buffered rather than emitted.
    ///
    /// Use ONLY around one-time initialization that other threads can block on
    /// (a `static let` singleton's initializer, a `dispatch_once` body). The
    /// buffered lines are flushed after `body` returns, on a background queue
    /// so the flush itself cannot stall the caller either.
    ///
    /// Re-entrant: nested calls share one buffer and only the outermost flushes.
    static func withDeferredLogging<T>(_ body: () throws -> T) rethrows -> T {
        let box = deferralBoxCreating()
        box.depth += 1

        defer {
            box.depth -= 1
            let shouldFlush = box.depth == 0
            let pending = shouldFlush ? box.buffer : []
            if shouldFlush {
                box.buffer.removeAll(keepingCapacity: false)
                Thread.current.threadDictionary.removeObject(forKey: deferralDepthKey)
            }

            if !pending.isEmpty {
                // Off the caller's thread on purpose: the caller may still be
                // holding the once token, and the whole point is that nothing
                // holding it touches the logging stack.
                DispatchQueue.global(qos: .utility).async {
                    for (category, level, message) in pending {
                        AppLogger(category: category).emit(level, message)
                    }
                }
            }
        }

        return try body()
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
