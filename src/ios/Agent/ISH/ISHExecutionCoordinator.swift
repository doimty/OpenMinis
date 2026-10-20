import Foundation
import SQLite3
import os.log

private let logger = AppLogger(category: "ISHCoordinator")

/// Result of a coordinated shell command execution.
struct ISHCommandResult {
    let output: String
    let exitCode: Int
}

/// Errors specific to the execution coordinator.
enum ISHCoordinatorError: Error, LocalizedError {
    case kernelNotBooted
    case noSession

    var errorDescription: String? {
        switch self {
        case .kernelNotBooted: return "iSH kernel is not booted"
        case .noSession: return "No session ID provided"
        }
    }
}

/// Global coordinator for iSH shell execution. Commands run concurrently
/// — across sessions AND within the same session — because each
/// `executeExecutable("/bin/sh", stdinData:…, fsContext:…)` call forks
/// a fresh sh process with its own PID, its own stdin/stdout/stderr
/// pipes, and a per-process fs_context routing token. There is no
/// shared PTY or shell instance to serialize behind.
///
/// Stop uses execution identities, including launches with no PID yet.
/// The historical FIFO wait-for-head logic was removed
/// 2026-05-25 because it made concurrent tool dispatch (TaskGroup
/// fan-out of N shell_execute calls) silently serialize at the
/// coordinator, defeating the upper-layer parallelism.
actor ISHExecutionCoordinator {
    static let shared = ISHExecutionCoordinator()

    /// [T-ish-shell-timeout-preserve-output] Cap on the partial output a
    /// timed-out command may hand back.
    ///
    /// Only bounds the in-memory mirror kept for the timeout path; the caller
    /// (`AIChatViewModel+ISHCommand.runRaw`) still applies its own head+tail
    /// truncation to whatever comes back, so this is not the model-facing
    /// limit. Sized well above that one so the mirror never becomes the
    /// binding constraint in practice, while still stopping a command that
    /// prints in a tight loop for its entire timeout from growing this
    /// without bound.
    static let kMaxPartialOutputChars = 256_000

    // MARK: - State

    /// Tracks the most recently mounted session — kept for backward compat
    /// with consumers that still rely on `mountedSessionIdSnapshot`. Once the
    /// change-event tracker is fully migrated to fs_context based routing
    /// this field can go away.
    private(set) var mountedSessionId: String? {
        didSet {
            Self.mountedSidLock.lock()
            Self.mountedSidStorage = mountedSessionId
            Self.mountedSidLock.unlock()
        }
    }

    /// Set of sessions for which the static (memory/skills/shared/external)
    /// mount layer has been initialized. The per-session 3 buckets are now
    /// handled by MinisFsRouter's hook, not by bind_mount; workspace is part
    /// of the static global mount layer.
    private var staticMountsInitialized: Set<String> = []

    /// Maps linux mount paths (e.g. "/var/minis/memory") to their host
    /// persistent URLs. Holds the global mounts (workspace/memory/skills/shared)
    /// and external folders. Per-session buckets are routed by MinisFsRouter.
    private var mountedPaths: [String: URL] = [:]

    // MARK: - Constants


    // MARK: - Public API

    /// Execute a shell command within a session's mount context.
    /// Fully concurrent — both cross-session AND same-session calls fan
    /// out in parallel. Each invocation forks an independent /bin/sh
    /// process with its own pipes and fs_context token; there is no
    /// shared PTY to serialize behind.
    /// [T-concurrent-tools 2026-05-25]
    /// Deadline and Stop are installed before actor/mount/launch work begins.
    /// A stuck kernel can exhaust the bounded launch capacity, but cannot own
    /// the caller's continuation or make us spawn an unbounded set of workers.
    nonisolated func execute(
        sessionId: String,
        command: String,
        timeout: TimeInterval?,
        lineCallback: @escaping (String) -> Void,
        pidCallback: @escaping (Int32) -> Void
    ) async throws -> ISHCommandResult {
        try Task.checkCancellation()
        guard !sessionId.isEmpty else { throw ISHCoordinatorError.noSession }
        guard ISHKernel.shared.isBooted else { throw ISHCoordinatorError.kernelNotBooted }
        let context = MinisFsRouter.shared.context(for: sessionId)
        guard context != 0,
              let ticket = ISHShellExecutor.makeTicket(fsContext: context) else {
            return ISHCommandResult(output: "[Shell execution identity unavailable; nothing was launched.]", exitCode: -1)
        }
        let id = UUID()
        let state = ISHCommandLifecycle(
            timeout: timeout ?? 300,
            maxPartialChars: Self.kMaxPartialOutputChars,
            onPID: { pid in
                // Share main-queue FIFO delivery with lineCallback at the
                // production boundary. Never wait for this queue to terminate.
                DispatchQueue.main.async {
                    if pid > 0 && !Self.isExecutionLive(sessionId: sessionId, id: id) { return }
                    pidCallback(pid)
                }
            },
            requestCleanup: { timedOut in
                ISHShellExecutor.requestTermination(ticket, error: timedOut ? .timeout : .cancelled)
            }
        )
        guard Self.reserveExecution(state, sessionId: sessionId, id: id) else {
            return ISHCommandResult(output: "[Shell execution capacity exhausted; earlier launches or cleanup may still be pending. Nothing was launched.]", exitCode: -1)
        }
        let (result, wasCancelled): (ISHCommandResult, Bool) = await withTaskCancellationHandler(operation: {
            await withCheckedContinuation { continuation in
                state.bindCompletion { outcome in
                    Self.removeExecution(sessionId: sessionId, id: id)
                    continuation.resume(returning: (
                        ISHCommandResult(output: outcome.output, exitCode: outcome.exitCode),
                        outcome.wasCancelled
                    ))
                }
                state.armDeadline()
                if Task.isCancelled { state.cancel() }
                Task.detached(priority: .userInitiated) { [self] in
                    defer { Self.pipelineFinished(id: id) }
                    guard !state.isTerminal else { return }
                    // A blocked mount may occupy this actor, not the deadline
                    // or Stop path. The reservation remains held until this
                    // pipeline REALLY ends, including after caller timeout.
                    await prepareCommand(sessionId: sessionId, state: state)
                    guard !state.isTerminal else { return }
                    await withCheckedContinuation { (launched: CheckedContinuation<Void, Never>) in
                        Self.launchQueue.addOperation {
                            defer { launched.resume() }
                            guard !state.isTerminal else { return }
                            Self.launchCommand(command: command, ticket: ticket, state: state, lineCallback: lineCallback)
                        }
                    }
                }
            }
        }, onCancel: {
            state.cancel()
        })
        // Preserve the legacy throwing contract for Task cancellation, while
        // keeping timeout/failure outcomes as values. A cancellation arriving
        // after normal completion wins cannot replace that completed result.
        if wasCancelled && Task.isCancelled { throw CancellationError() }
        return result
    }

    private func prepareCommand(sessionId: String, state: ISHCommandLifecycle) {
        guard !state.isTerminal else { return }
        mountedSessionId = sessionId
        ensureStaticMountsInitialized(for: sessionId)
    }


    /// Called from loadSession() for UI readiness. Per-session buckets are
    /// routed by MinisFsRouter's hook now (no bind-mount swap needed), so
    /// this just kicks off the one-time static mount layer and remembers
    /// the most-recently-active session for backward-compat consumers.
    func mountForSession(_ sessionId: String) {
        // [MOUNT-DIAG] So we can see the exact ordering between session load
        // and the deferred external-mount snapshot apply.
        let booted = ISHKernel.shared.isBooted
        let snapCount = Self.getExternalMountSnapshot().count
        logger.info("MOUNT-DIAG mountForSession-entry sid=\(sessionId) booted=\(booted) externalSnapshotCount=\(snapCount) staticInit=\(self.staticMountsInitialized.count)")
        guard booted else {
            logger.warning("MOUNT mountForSession(\(sessionId)) skipped — kernel not booted")
            mountedSessionId = nil
            return
        }
        _ = MinisFsRouter.shared.context(for: sessionId)
        ensureStaticMountsInitialized(for: sessionId)
        mountedSessionId = sessionId
    }

    /// Ensure the static mount layer (memory/skills/shared + external folders)
    /// is in place. Safe to call repeatedly; idempotent at the iSH kernel level.
    func ensureMounted(for sessionId: String) {
        guard ISHKernel.shared.isBooted else {
            logger.warning("MOUNT ensureMounted(\(sessionId)) skipped — kernel not booted")
            return
        }
        ensureStaticMountsInitialized(for: sessionId)
        if mountedSessionId == nil { mountedSessionId = sessionId }
    }

    private func ensureStaticMountsInitialized(for sessionId: String) {
        // Static mounts are session-independent, but performMount needs a sid
        // to log against; once the first session has initialized them, all
        // others are no-ops. Workspace is global and is mounted exactly once;
        // offloads/attachments/browser are handled by MinisFsRouter.
        if !staticMountsInitialized.isEmpty { return }
        performMount(sessionId)
        staticMountsInitialized.insert(sessionId)
    }

    #if DEBUG
    /// DEBUG-only snapshot of the current mount table, for diagnostic logging
    /// from call sites that need to print mount state.
    func debugMountSnapshot() -> (sessionId: String?, paths: [String: URL]) {
        return (mountedSessionId, mountedPaths)
    }
    #endif

    /// Resolve a Linux path under /var/minis/ to its current host URL.
    /// Tries the static bind-mount table first (workspace/memory/skills/shared +
    /// external folders), then falls back to MinisFsRouter for the remaining
    /// per-session buckets.
    func hostURL(for linuxPath: String, sessionId: String? = nil) -> URL? {
        for (prefix, hostBase) in mountedPaths {
            if linuxPath == prefix {
                return hostBase
            }
            if linuxPath.hasPrefix(prefix + "/") {
                let sub = String(linuxPath.dropFirst(prefix.count + 1))
                return hostBase.appendingPathComponent(sub)
            }
        }
        if let sid = sessionId ?? mountedSessionId,
           let url = MinisFsRouter.shared.hostURL(forGuest: linuxPath, sid: sid) {
            return url
        }
        return nil
    }

    /// Kill all currently-running commands across every session. Kept as the
    /// "broad stop" used by emergency cancel paths.
    func stopCurrentCommand() {
        Self.stopAllNonisolated()
    }

    /// Kill any in-flight command(s) for the given session only. Used by the
    /// per-session cancel path.
    func stopCurrentCommand(sessionId: String) {
        Self.stopAllNonisolated(sessionId: sessionId)
    }

    /// Cancel local executions when a session is deleted or cleared.
    /// Process cleanup is requested separately and may still be pending.
    func sessionDidTerminate(sessionId: String) {
        Self.stopAllNonisolated(sessionId: sessionId)
        staticMountsInitialized.remove(sessionId)
        if mountedSessionId == sessionId { mountedSessionId = nil }
    }

    // MARK: - Private

    /// Host-only preparation/launch is isolated from the caller's deadline.
    /// At most two jobs run here, with at most 32 reservations in total.
    private static let launchQueue: OperationQueue = {
        let queue = OperationQueue()
        queue.name = "com.openminis.ish.launch"
        queue.maxConcurrentOperationCount = 2
        queue.qualityOfService = .userInitiated
        return queue
    }()

    private static func launchCommand(
        command: String,
        ticket: ISHShellExecutionTicket,
        state: ISHCommandLifecycle,
        lineCallback: @escaping (String) -> Void
    ) {
        guard !state.isTerminal else { return }
        let customEnv = EnvVarStore.shared.allAsDict()
        guard !state.isTerminal else { return }
        // Preserve /root cwd, subshell isolation, and a newline before the
        // closing parenthesis so a final heredoc terminator stays on its line.
        let script = "cd /root\n({ exec 0</dev/null; } 2>/dev/null || true; \(command)\n)\n"
        guard let stdin = script.data(using: .utf8) else {
            state.failToLaunch("The command could not be encoded as UTF-8.")
            return
        }
        let pid = ISHShellExecutor.executeExecutable(
            "/bin/sh", arguments: [], environment: customEnv,
            stdinData: stdin, executionTicket: ticket,
            lineCallback: { line, _ in
                state.receiveLine(line) { delivered in
                    // runRaw's client uses MainActor.assumeIsolated. Keep
                    // the existing main-thread contract at this boundary,
                    // and suppress callbacks queued before terminal.
                    DispatchQueue.main.async {
                        guard !state.isTerminal else { return }
                        lineCallback(delivered)
                    }
                }
            },
            completion: { result in
                state.finish(exitCode: Int(result.exitCode), stdout: result.output, stderr: result.errorOutput)
            }
        )
        if pid > 0 {
            state.publishPID(pid)
        } else {
            state.failToLaunch("The shell could not be started (error \(pid)).")
        }
    }




    // MARK: - Mount Logic

    /// Bind-mount a session's minis directories into iSH-visible /var/minis/.
    private func performMount(_ sid: String) {
        let mountStart = CFAbsoluteTimeGetCurrent()
        let fm = FileManager.default

        logger.info("MOUNT START for session \(sid)")

        // [MOUNT-DIAG] Capture external-snapshot state as performMount sees it.
        // If this prints 0 entries on a cold launch where the user has mounts
        // configured, the cold-start race is confirmed: performMount ran
        // before MountedFoldersManager.activateAll's resolve task pushed the
        // snapshot. The subsequent applyExternalMountSnapshot() (kicked from
        // pushExternalMountSnapshot once resolve finishes) should fix it up.
        let extSnap = Self.getExternalMountSnapshot()
        let booted = ISHKernel.shared.isBooted
        logger.info("MOUNT-DIAG performMount-entry sid=\(sid) booted=\(booted) snapshotCount=\(extSnap.count) staticInit=\(self.staticMountsInitialized.count)")

        // Ensure parent dirs exist in meta.db
        ensureParentDirsInMetaDB(for: "\(AIChatViewModel.minisLinuxBaseDir)/placeholder")
        ensureFakefsMetadata(for: AIChatViewModel.minisLinuxBaseDir, isDirectory: true)

        // Only the global (cross-session) directories are bind-mounted here.
        // workspace is deliberately global as well; the remaining three
        // per-session buckets are routed dynamically by MinisFsRouter.
        let subdirs: [(persistDir: URL, linuxDir: String)] = [
            (AIChatViewModel.minisGlobalWorkspacePersistentDir, AIChatViewModel.minisWorkspaceLinuxDir),
            (AIChatViewModel.minisMemoryPersistentDir, AIChatViewModel.minisMemoryLinuxDir),
            (AIChatViewModel.minisSkillsPersistentDir, AIChatViewModel.minisSkillsLinuxDir),
            (AIChatViewModel.minisSharedPersistentDir, AIChatViewModel.minisSharedLinuxDir),
            (AIChatViewModel.minisMcpServersPersistentDir, AIChatViewModel.minisMcpServersLinuxDir),
        ]

        for (idx, (persistDir, linuxDir)) in subdirs.enumerated() {
            logger.info("MOUNT [\(idx)] === Mounting subdir ===")
            logger.info("MOUNT [\(idx)] linuxDir        = \"\(linuxDir)\"")
            logger.info("MOUNT [\(idx)] linuxDir.count  = \(linuxDir.count)")
            logger.info("MOUNT [\(idx)] linuxDir bytes  = \(Array(linuxDir.utf8).map { String(format: "%02x", $0) }.joined(separator: " "))")
            logger.info("MOUNT [\(idx)] persistDir      = \"\(persistDir.path)\"")
            logger.info("MOUNT [\(idx)] persistDir.count= \(persistDir.path.count)")

            // Ensure persistent directory exists on host
            try? fm.createDirectory(at: persistDir, withIntermediateDirectories: true)

            // Remove old data directory or stale symlink so fakefs_bind_mount can succeed.
            // We must clear the path at data/<linuxDir> so that the C layer can create
            // a symlink there pointing to persistDir.
            if let hostDir = resolveHostPath(linuxDir) {
                logger.info("MOUNT [\(idx)] resolved hostDir = \"\(hostDir.path)\"")
                // Use lstat (not stat) so we can distinguish symlinks from directories.
                // FileManager.attributesOfItem follows symlinks, which breaks detection.
                var lstatBuf = stat()
                let lstatOk = lstat(hostDir.path, &lstatBuf) == 0

                if lstatOk && (lstatBuf.st_mode & S_IFMT) == S_IFLNK {
                    // It's a symlink — check if it resolves to the right place.
                    // Use resolvingSymlinksInPath on both sides to normalize aliases
                    // (e.g. GroupContainersAlias vs "Group Containers" on iOS).
                    let resolvedDest = hostDir.resolvingSymlinksInPath().standardized.path
                    let expectedDest = persistDir.resolvingSymlinksInPath().standardized.path
                    if resolvedDest != expectedDest {
                        logger.info("MOUNT [\(idx)] removing stale symlink (resolved to \(resolvedDest), expected \(expectedDest))")
                        unlink(hostDir.path)
                    } else {
                        logger.info("MOUNT [\(idx)] symlink already correct, skipping removal")
                    }
                } else if lstatOk {
                    // Real file or directory — migrate contents to persistent storage, then remove
                    let isDir = (lstatBuf.st_mode & S_IFMT) == S_IFDIR
                    if isDir {
                        logger.info("MOUNT [\(idx)] migrating real dir to persistDir")
                        if let enumerator = fm.enumerator(at: hostDir, includingPropertiesForKeys: nil, options: []) {
                            while let srcURL = enumerator.nextObject() as? URL {
                                let rel = srcURL.path.replacingOccurrences(of: hostDir.path, with: "")
                                let dstURL = persistDir.appendingPathComponent(rel)
                                if !fm.fileExists(atPath: dstURL.path) {
                                    try? fm.createDirectory(at: dstURL.deletingLastPathComponent(), withIntermediateDirectories: true)
                                    try? fm.copyItem(at: srcURL, to: dstURL)
                                }
                            }
                        }
                    }
                    // Force-remove whatever is at hostDir (file, dir, or non-empty dir)
                    // Safety: only remove paths under data/var/minis/ to avoid accidentally
                    // deleting other fakefs directories (e.g. data/dev/) due to path bugs.
                    let dataVarMinis = RootfsManager.shared.dataPath.appendingPathComponent("var/minis").path
                    guard hostDir.path.hasPrefix(dataVarMinis) else {
                        logger.error("MOUNT [\(idx)] SAFETY: refusing removeItem outside var/minis: \(hostDir.path)")
                        break
                    }
                    let kind = isDir ? "dir" : "file"
                    logger.info("MOUNT [\(idx)] removing real \(kind) at \(hostDir.path)")
                    do {
                        try fm.removeItem(at: hostDir)
                    } catch {
                        logger.error("MOUNT [\(idx)] removeItem failed: \(error.localizedDescription)")
                    }
                }
            }

            // Bind mount: creates symlink in data/ pointing to persistDir
            // Log files in the persistent directory before bind mount
            let persistContents = (try? fm.contentsOfDirectory(atPath: persistDir.path)) ?? []
            logger.info("MOUNT [\(idx)] persistDir has \(persistContents.count) items: \(persistContents.joined(separator: ", "))")

            logger.info("MOUNT [\(idx)] calling bindMountPath(\"\(linuxDir)\", toHostPath: \"\(persistDir.path)\")")
            let err = ISHKernel.shared.bindMountPath(linuxDir, toHostPath: persistDir.path)
            if err < 0 {
                logger.error("MOUNT [\(idx)] bind mount FAILED: \(linuxDir) -> \(persistDir.path) err=\(err)")
            } else {
                logger.info("MOUNT [\(idx)] bind mount OK: \(linuxDir) -> \(persistDir.path)")
                mountedPaths[linuxDir] = persistDir

                // Verify: list files visible via the host symlink path after mount
                if let hostDir = resolveHostPath(linuxDir) {
                    // Check what the symlink points to
                    var lstatBuf2 = stat()
                    if lstat(hostDir.path, &lstatBuf2) == 0 && (lstatBuf2.st_mode & S_IFMT) == S_IFLNK {
                        let symlinkTarget = try? fm.destinationOfSymbolicLink(atPath: hostDir.path)
                        logger.info("MOUNT [\(idx)] symlink at \(hostDir.path) -> \(symlinkTarget ?? "nil")")
                    } else {
                        logger.info("MOUNT [\(idx)] hostDir \(hostDir.path) is NOT a symlink (or doesn't exist)")
                    }
                    let hostContents = (try? fm.contentsOfDirectory(atPath: hostDir.path)) ?? []
                    logger.info("MOUNT [\(idx)] hostDir after mount has \(hostContents.count) items: \(hostContents.joined(separator: ", "))")
                }

                // Register existing files in meta.db so iSH fakefs can see them (recursive)
                let metaStart = CFAbsoluteTimeGetCurrent()
                let registered = registerFakefsMetadataRecursively(for: persistDir, linuxPrefix: linuxDir)
                let metaMs = (CFAbsoluteTimeGetCurrent() - metaStart) * 1000
                logger.info("MOUNT [\(idx)] registered \(registered) meta.db entries (batch) in \(String(format: "%.1f", metaMs))ms")
            }
        }

        // After session-scoped mounts, also bind-mount every user-mounted
        // external folder (see MountedFoldersManager) so iSH shell can read/write
        // them just like /var/minis/shared.
        mountExternalFoldersLocked()

        let elapsed = (CFAbsoluteTimeGetCurrent() - mountStart) * 1000
        logger.info("MOUNT bind-mounted \(subdirs.count) dirs for session \(sid) in \(String(format: "%.1f", elapsed))ms")
    }

    /// One entry in an external-folder mount snapshot.
    struct ExternalMountSpec: Sendable {
        let linuxDir: String
        let hostPath: String
        /// When true, iSH will expose this mount as a read-only directory
        /// (mode 0555), so shell writes fail with EACCES.
        let readOnly: Bool
    }

    /// Thread-safe shared snapshot of external-folder mounts. Written by
    /// MountedFoldersManager (main actor) and read by the coordinator actor
    /// during `performMount`. Using a lock instead of actor-isolated state
    /// avoids cross-actor hops during synchronous mount code.
    private static let externalMountLock = NSLock()
    nonisolated(unsafe) private static var externalMountSnapshotStorage: [ExternalMountSpec] = []

    /// Lock-protected mirror of `mountedSessionId`, kept in sync by the
    /// actor's `didSet` observer. Exposed via `mountedSessionIdSnapshot` so
    /// callers running on other actors (e.g. `BrowserUseOffloadBridge` on the
    /// main actor) can read the current mount owner without awaiting this
    /// actor. Without this, every cross-actor peek was a suspension point
    /// that serialized behind whatever was queued on the coordinator —
    /// which, during shell execution, is a continuation parked on the
    /// main-queue completion hop, starving the bridge's MainActor task.
    private static let mountedSidLock = NSLock()
    nonisolated(unsafe) private static var mountedSidStorage: String?

    /// Independent of the actor and kernel queues. Includes preparing commands
    /// whose PID is not available yet, so Stop does not miss late launches.
    private static let executionLock = NSLock()
    nonisolated(unsafe) private static var executionStorage: [String: [UUID: ISHCommandLifecycle]] = [:]
    // Bit 0: caller terminal. Bit 1: launch pipeline actually returned. A
    // timed-out but wedged pipeline must keep its capacity reservation.
    nonisolated(unsafe) private static var executionReservations: [UUID: UInt8] = [:]
    private static let maxExecutionReservations = 32

    private static func reserveExecution(_ state: ISHCommandLifecycle, sessionId: String, id: UUID) -> Bool {
        executionLock.lock()
        defer { executionLock.unlock() }
        guard executionReservations.count < maxExecutionReservations else { return false }
        executionReservations[id] = 0
        executionStorage[sessionId, default: [:]][id] = state
        return true
    }

    private static func isExecutionLive(sessionId: String, id: UUID) -> Bool {
        executionLock.lock()
        let state = executionStorage[sessionId]?[id]
        executionLock.unlock()
        return state.map { !$0.isTerminal } ?? false
    }

    private static func removeExecution(sessionId: String, id: UUID) {
        executionLock.lock()
        defer { executionLock.unlock() }
        executionStorage[sessionId]?[id] = nil
        if executionStorage[sessionId]?.isEmpty == true { executionStorage[sessionId] = nil }
        guard let bits = executionReservations[id] else { return }
        if (bits | 1) == 3 { executionReservations[id] = nil }
        else { executionReservations[id] = bits | 1 }
    }

    private static func pipelineFinished(id: UUID) {
        executionLock.lock()
        defer { executionLock.unlock() }
        guard let bits = executionReservations[id] else { return }
        if (bits | 2) == 3 { executionReservations[id] = nil }
        else { executionReservations[id] = bits | 2 }
    }

    /// Count of local cancellation requests, NOT processes confirmed killed.
    @discardableResult
    nonisolated static func stopAllNonisolated(sessionId: String? = nil) -> Int {
        executionLock.lock()
        let targets = executionStorage.filter { sessionId == nil || $0.key == sessionId }
            .flatMap { Array($0.value.values) }
        executionLock.unlock()
        for state in targets { state.cancel() }
        return targets.count
    }

    /// Read the current mount owner without an actor hop. Safe to call from
    /// any thread. Returns nil when no session is mounted (kernel not booted
    /// or session just terminated).
    nonisolated static var mountedSessionIdSnapshot: String? {
        mountedSidLock.lock()
        defer { mountedSidLock.unlock() }
        return mountedSidStorage
    }

    static func setExternalMountSnapshot(_ snapshot: [ExternalMountSpec]) {
        externalMountLock.lock()
        externalMountSnapshotStorage = snapshot
        externalMountLock.unlock()
    }

    private static func getExternalMountSnapshot() -> [ExternalMountSpec] {
        externalMountLock.lock()
        defer { externalMountLock.unlock() }
        return externalMountSnapshotStorage
    }

    /// Tracks the set of external-folder linux paths currently bind-mounted so
    /// we can unbind stale ones when the user renames or removes a mount.
    private var externalMountLinuxPaths: Set<String> = []

    /// Apply the current shared snapshot to the kernel bind-mount table.
    /// Called by MountedFoldersManager after it updates the shared snapshot.
    /// No-op if kernel not booted (the snapshot will be applied on the next
    /// performMount when shell execution begins).
    func applyExternalMountSnapshot() {
        // [MOUNT-DIAG] Caller-side dump so we can see exactly what state the
        // coordinator was in when MountedFoldersManager pushed a snapshot —
        // particularly whether the kernel had booted by the time we got
        // here (vs the push falling on deaf ears at cold start).
        let snapshot = Self.getExternalMountSnapshot()
        let booted = ISHKernel.shared.isBooted
        logger.info("MOUNT-DIAG apply-entry snapshotCount=\(snapshot.count) booted=\(booted) staticInit=\(self.staticMountsInitialized.count) currentLinuxPaths=\(self.externalMountLinuxPaths.sorted())")
        for (i, e) in snapshot.enumerated() {
            logger.info("MOUNT-DIAG apply-entry [\(i)] \(e.linuxDir) -> \(e.hostPath) (ro=\(e.readOnly))")
        }
        guard booted else {
            logger.info("MOUNT external: kernel not booted, will apply snapshot on next performMount")
            return
        }
        mountExternalFoldersLocked()
    }

    /// Internal: actually perform the external-folder bind mounts.
    /// Reconciles the bind-mount table against the shared snapshot —
    /// unbinds stale ones, binds new ones, updates changed host paths.
    /// Caller guarantees kernel is booted.
    private func mountExternalFoldersLocked() {
        let snapshot = Self.getExternalMountSnapshot()
        logger.info("MOUNT external: applying snapshot with \(snapshot.count) entries")
        let desiredPaths = Set(snapshot.map(\.linuxDir))

        // Unbind any stale external mounts (renamed or removed entries).
        for stale in externalMountLinuxPaths.subtracting(desiredPaths) {
            let err = ISHKernel.shared.bindUnmountPath(stale)
            if err < 0 {
                logger.warning("MOUNT external: bindUnmountPath(\(stale)) err=\(err)")
            } else {
                logger.info("MOUNT external: unbound stale \(stale)")
            }
            mountedPaths.removeValue(forKey: stale)
        }
        externalMountLinuxPaths = desiredPaths

        guard !snapshot.isEmpty else { return }

        // Ensure the parent /var/minis/mounts dir exists in meta.db so fakefs
        // can list it.
        ensureParentDirsInMetaDB(for: "\(AIChatViewModel.minisMountsLinuxDir)/placeholder")
        ensureFakefsMetadata(for: AIChatViewModel.minisMountsLinuxDir, isDirectory: true)

        for (idx, entry) in snapshot.enumerated() {
            logger.info("MOUNT external [\(idx)] \(entry.linuxDir) -> \(entry.hostPath) (ro=\(entry.readOnly))")

            // [MOUNT-DIAG] Verify the host path is reachable from this process
            // BEFORE asking the kernel to bind it. Reports stat result + iCloud
            // FileProvider container state — common failure shapes:
            //   - ENOENT: bookmark resolved to a path that no longer exists
            //   - EPERM: security scope wasn't acquired (or expired)
            //   - EACCES: scope acquired but parent dir not readable yet (XPC
            //             still warming up after cold start)
            var hostStat = stat()
            let statRC = stat(entry.hostPath, &hostStat)
            if statRC == 0 {
                let isDir = (hostStat.st_mode & S_IFMT) == S_IFDIR
                logger.info("MOUNT-DIAG host-pre-check [\(idx)] OK isDir=\(isDir) mode=0o\(String(hostStat.st_mode & 0o777, radix: 8)) size=\(hostStat.st_size)")
            } else {
                let errStr = String(cString: strerror(errno))
                logger.error("MOUNT-DIAG host-pre-check [\(idx)] stat() FAILED errno=\(errno) (\(errStr)) — host path unreachable from app sandbox; bind will likely still 'succeed' but reads will fail")
            }

            let bindStart = CFAbsoluteTimeGetCurrent()
            let err = ISHKernel.shared.bindMountPath(
                entry.linuxDir,
                toHostPath: entry.hostPath,
                readOnly: entry.readOnly
            )
            let bindMs = (CFAbsoluteTimeGetCurrent() - bindStart) * 1000
            if err < 0 {
                logger.error("MOUNT external [\(idx)] bind mount FAILED err=\(err) bindMs=\(String(format: "%.1f", bindMs))")
                continue
            }
            logger.info("MOUNT-DIAG bind [\(idx)] OK err=\(err) bindMs=\(String(format: "%.1f", bindMs))")
            mountedPaths[entry.linuxDir] = URL(fileURLWithPath: entry.hostPath)

            // Register the top-level mount dir in meta.db. We deliberately do
            // NOT enumerate + register every file recursively: external vaults
            // can be huge, and fakefs auto-creates inodes on demand via
            // bind_mount_ensure_inode() when the shell actually touches a path.
            ensureFakefsMetadata(for: entry.linuxDir, isDirectory: true)

            // [MOUNT-DIAG] Post-bind verification: try to list one host-path
            // entry. If this comes back empty/fails after a successful stat
            // above, the security scope likely lapsed between resolve and bind.
            if let urls = try? FileManager.default.contentsOfDirectory(at: URL(fileURLWithPath: entry.hostPath), includingPropertiesForKeys: nil) {
                let names = urls.prefix(3).map { $0.lastPathComponent }
                logger.info("MOUNT-DIAG host-post-check [\(idx)] readable, \(urls.count) entries — sample: \(names)")
            } else {
                logger.error("MOUNT-DIAG host-post-check [\(idx)] contentsOfDirectory FAILED — host scope likely not held; iSH `ls` will return empty")
            }
        }

        logger.info("MOUNT external: bind-mounted \(snapshot.count) external folder(s)")
    }

    // MARK: - Fakefs Metadata (inline copies for coordinator use)

    private func resolveHostPath(_ linuxPath: String) -> URL? {
        guard linuxPath.hasPrefix("/"), !linuxPath.contains("..") else { return nil }
        let relative = String(linuxPath.dropFirst())
        if relative.isEmpty {
            return RootfsManager.shared.dataPath
        }
        return RootfsManager.shared.dataPath.appendingPathComponent(relative)
    }

    private static let SQLITE_TRANSIENT = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

    private func bindPathBlob(_ stmt: OpaquePointer, index: Int32, path: String) {
        let utf8 = Array(path.utf8)
        sqlite3_bind_blob(stmt, index, utf8, Int32(utf8.count), Self.SQLITE_TRANSIENT)
    }

    private func ensureFakefsMetadata(for linuxPath: String, isDirectory: Bool) {
        let metaDBPath = RootfsManager.shared.rootfsPath.appendingPathComponent("meta.db").path
        var db: OpaquePointer?
        guard sqlite3_open_v2(metaDBPath, &db, SQLITE_OPEN_READWRITE, nil) == SQLITE_OK,
              let db else { return }
        defer { sqlite3_close(db) }

        var checkStmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, "SELECT inode FROM paths WHERE path = ?", -1, &checkStmt, nil) == SQLITE_OK,
              let checkStmt else { return }
        defer { sqlite3_finalize(checkStmt) }

        bindPathBlob(checkStmt, index: 1, path: linuxPath)

        if sqlite3_step(checkStmt) == SQLITE_ROW {
            return
        }

        let mode: UInt32 = isDirectory ? 0o040755 : 0o100644
        var statBytes: [UInt8] = Array(repeating: 0, count: 16)
        let leMode = mode.littleEndian
        withUnsafeBytes(of: leMode) { src in
            for i in 0..<4 { statBytes[i] = src[i] }
        }

        var insertStatStmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, "INSERT INTO stats (stat) VALUES (?)", -1, &insertStatStmt, nil) == SQLITE_OK,
              let insertStatStmt else { return }
        defer { sqlite3_finalize(insertStatStmt) }

        sqlite3_bind_blob(insertStatStmt, 1, statBytes, Int32(statBytes.count), Self.SQLITE_TRANSIENT)
        guard sqlite3_step(insertStatStmt) == SQLITE_DONE else { return }

        var insertPathStmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, "INSERT OR REPLACE INTO paths (path, inode) VALUES (?, last_insert_rowid())", -1, &insertPathStmt, nil) == SQLITE_OK,
              let insertPathStmt else { return }
        defer { sqlite3_finalize(insertPathStmt) }

        bindPathBlob(insertPathStmt, index: 1, path: linuxPath)
        sqlite3_step(insertPathStmt)
    }

    private func ensureParentDirsInMetaDB(for linuxPath: String) {
        var current = (linuxPath as NSString).deletingLastPathComponent
        var dirs: [String] = []
        while current != "/" && !current.isEmpty {
            dirs.append(current)
            current = (current as NSString).deletingLastPathComponent
        }
        for dir in dirs.reversed() {
            ensureFakefsMetadata(for: dir, isDirectory: true)
        }
    }

    // MARK: - Batch Fakefs Metadata

    /// [T-ios-skill-backup-fakefs] Register `hostDir` itself and everything
    /// under it (recursively, hidden files skipped) in meta.db as
    /// `linuxPrefix[/relative]`, in one batched transaction. Returns the
    /// number of entries handed to the batch (existing rows are skipped
    /// inside it, so calling this twice is harmless).
    ///
    /// Extracted from performMount so that paths which write files into a
    /// bind-mounted host directory OUTSIDE the mount pass — backup restore of
    /// a skill's bundled scripts, for instance — can make them visible to the
    /// guest immediately instead of after the next cold start's mount walk.
    @discardableResult
    func registerFakefsMetadataRecursively(for hostDir: URL, linuxPrefix: String) -> Int {
        let fm = FileManager.default
        var metaEntries: [(path: String, isDirectory: Bool)] = [(linuxPrefix, true)]
        if let enumerator = fm.enumerator(at: hostDir, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles]) {
            while let itemURL = enumerator.nextObject() as? URL {
                let relativePath = itemURL.path.dropFirst(hostDir.path.count + 1) // strip prefix + "/"
                let linuxFilePath = "\(linuxPrefix)/\(relativePath)"
                let isDir = (try? itemURL.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) ?? false
                metaEntries.append((linuxFilePath, isDir))
            }
        }
        batchEnsureFakefsMetadata(metaEntries)
        return metaEntries.count
    }

    /// Register multiple paths in meta.db within a single SQLite connection and transaction.
    /// Much faster than calling `ensureFakefsMetadata` per-file for large directories.
    private func batchEnsureFakefsMetadata(_ entries: [(path: String, isDirectory: Bool)]) {
        guard !entries.isEmpty else { return }

        let metaDBPath = RootfsManager.shared.rootfsPath.appendingPathComponent("meta.db").path
        var db: OpaquePointer?
        guard sqlite3_open_v2(metaDBPath, &db, SQLITE_OPEN_READWRITE, nil) == SQLITE_OK,
              let db else { return }
        defer { sqlite3_close(db) }

        // Prepare reusable statements
        var checkStmt: OpaquePointer?
        var insertStatStmt: OpaquePointer?
        var insertPathStmt: OpaquePointer?

        guard sqlite3_prepare_v2(db, "SELECT inode FROM paths WHERE path = ?", -1, &checkStmt, nil) == SQLITE_OK,
              let checkStmt else { return }
        defer { sqlite3_finalize(checkStmt) }

        guard sqlite3_prepare_v2(db, "INSERT INTO stats (stat) VALUES (?)", -1, &insertStatStmt, nil) == SQLITE_OK,
              let insertStatStmt else { return }
        defer { sqlite3_finalize(insertStatStmt) }

        guard sqlite3_prepare_v2(db, "INSERT OR REPLACE INTO paths (path, inode) VALUES (?, last_insert_rowid())", -1, &insertPathStmt, nil) == SQLITE_OK,
              let insertPathStmt else { return }
        defer { sqlite3_finalize(insertPathStmt) }

        // Pre-compute stat blobs for file and directory
        func makeStatBlob(mode: UInt32) -> [UInt8] {
            var bytes: [UInt8] = Array(repeating: 0, count: 16)
            let le = mode.littleEndian
            withUnsafeBytes(of: le) { src in
                for i in 0..<4 { bytes[i] = src[i] }
            }
            return bytes
        }
        let dirStatBlob = makeStatBlob(mode: 0o040755)
        let fileStatBlob = makeStatBlob(mode: 0o100644)

        // Begin transaction
        sqlite3_exec(db, "BEGIN TRANSACTION", nil, nil, nil)

        var inserted = 0
        for (path, isDir) in entries {
            // Check if already exists
            sqlite3_reset(checkStmt)
            sqlite3_clear_bindings(checkStmt)
            bindPathBlob(checkStmt, index: 1, path: path)
            if sqlite3_step(checkStmt) == SQLITE_ROW { continue }

            // Insert stat
            let statBlob = isDir ? dirStatBlob : fileStatBlob
            sqlite3_reset(insertStatStmt)
            sqlite3_clear_bindings(insertStatStmt)
            sqlite3_bind_blob(insertStatStmt, 1, statBlob, Int32(statBlob.count), Self.SQLITE_TRANSIENT)
            guard sqlite3_step(insertStatStmt) == SQLITE_DONE else { continue }

            // Insert path
            sqlite3_reset(insertPathStmt)
            sqlite3_clear_bindings(insertPathStmt)
            bindPathBlob(insertPathStmt, index: 1, path: path)
            if sqlite3_step(insertPathStmt) == SQLITE_DONE {
                inserted += 1
            }
        }

        sqlite3_exec(db, "COMMIT", nil, nil, nil)
        logger.info("MOUNT batchEnsureFakefsMetadata: \(entries.count) checked, \(inserted) inserted")
    }
}
