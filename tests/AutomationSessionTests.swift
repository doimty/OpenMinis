import Darwin
import Foundation

@main
enum AutomationSessionTests {
    private static let commit = String(repeating: "a", count: 40)
    private static let run = "44900128255"
    private static let tick = 100_000.0

    static func main() throws {
        try authorization()
        try files()
        print("Production automation session: explicit confirmation, denial, expiry, stop, secure entropy, no replay/restore, and private atomic file policy: PASS")
    }

    private static func authorization() throws {
        var state = AutomationSessionState()
        precondition(state.session == nil && state.current(at: tick) == nil)
        precondition(state.requestStart(foreground: false) == nil)
        let denied = state.requestStart(foreground: true)!
        precondition(state.requestStart(foreground: true) == nil)
        var entropyCalls = 0
        let deterministic: () throws -> [UInt8] = { entropyCalls += 1; return [UInt8](repeating: 7, count: 32) }
        requireAbsent(try state.confirm(UUID(), allow: true, foreground: true, now: tick,
                                       commit: commit, runID: run, entropy: deterministic))
        precondition(state.pending == denied)
        requireAbsent(try state.confirm(denied, allow: false, foreground: true, now: tick,
                                       commit: commit, runID: run, entropy: deterministic))
        precondition(state.pending == nil && state.session == nil && entropyCalls == 0)
        requireAbsent(try state.confirm(denied, allow: true, foreground: true, now: tick,
                                       commit: commit, runID: run, entropy: deterministic))

        let lostForeground = state.requestStart(foreground: true)!
        requireAbsent(try state.confirm(lostForeground, allow: true, foreground: false, now: tick,
                                       commit: commit, runID: run, entropy: deterministic))
        precondition(entropyCalls == 0)
        let first = state.requestStart(foreground: true)!
        let session = try state.confirm(first, allow: true, foreground: true, now: tick,
                                         commit: commit, runID: run, entropy: deterministic)!
        precondition(session.expires - session.created == 180 && entropyCalls == 1)
        precondition(session.token.count == 64 && session.token.utf8.allSatisfy { (48...57).contains($0) || (97...102).contains($0) })
        precondition(session.schema == 1 && session.toolsVersion == 1 && UUID(uuidString: session.id) != nil)
        precondition(session.sourceCommit == commit && session.actionsRunID == run)
        precondition(state.requestStart(foreground: true) == nil)
        requireAbsent(try state.confirm(first, allow: true, foreground: true, now: tick,
                                       commit: commit, runID: run, entropy: deterministic))
        precondition(entropyCalls == 1 && state.current(at: tick + 179.999) == session)
        precondition(state.current(at: tick + 180) == nil && state.session == nil)
        precondition(state.current(at: tick + 181) == nil)
        precondition(AutomationSessionState().session == nil)

        let stopPending = state.requestStart(foreground: true)!
        state.stop()
        requireAbsent(try state.confirm(stopPending, allow: true, foreground: true, now: tick,
                                       commit: commit, runID: run, entropy: deterministic))
        let stopActive = state.requestStart(foreground: true)!
        _ = try state.confirm(stopActive, allow: true, foreground: true, now: tick, commit: commit, runID: run, entropy: deterministic)
        state.stop()
        precondition(state.current(at: tick + 1) == nil)
        let invalidEntropy = state.requestStart(foreground: true)!
        do {
            _ = try state.confirm(invalidEntropy, allow: true, foreground: true, now: tick,
                                  commit: commit, runID: run, entropy: { throw AutomationSessionFailure.randomUnavailable })
            preconditionFailure("entropy errors must refuse a grant")
        } catch AutomationSessionFailure.randomUnavailable {}
        precondition(state.session == nil && state.pending == nil)
        requireAbsent(try state.confirm(invalidEntropy, allow: true, foreground: true, now: tick,
                                       commit: commit, runID: run, entropy: deterministic))
        let shortEntropy = state.requestStart(foreground: true)!
        do {
            _ = try state.confirm(shortEntropy, allow: true, foreground: true, now: tick,
                                  commit: commit, runID: run, entropy: { [1, 2] })
            preconditionFailure("short entropy must refuse a grant")
        } catch AutomationSessionFailure.randomUnavailable {}
        let badBuild = state.requestStart(foreground: true)!
        do {
            _ = try state.confirm(badBuild, allow: true, foreground: true, now: tick,
                                  commit: "not-a-build", runID: run, entropy: deterministic)
            preconditionFailure("invalid build identity must refuse a grant")
        } catch AutomationSessionFailure.invalidIdentity {}
        let fresh = state.requestStart(foreground: true)!
        let randomSession = try state.confirm(fresh, allow: true, foreground: true, now: tick,
                                               commit: commit, runID: run)!
        precondition(randomSession.token.count == 64 && randomSession.token != session.token && randomSession.id != session.id)
        precondition(state.current(at: tick - 1) == nil)
    }

    private static func files() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("minis-automation-session-" + UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false)
        defer { try? FileManager.default.removeItem(at: root) }
        let directory = root.appendingPathComponent("control")
        let store = AutomationSessionFile(directory: directory.path, owner: getuid())
        var state = AutomationSessionState()
        let request = state.requestStart(foreground: true)!
        let grant = try state.confirm(request, allow: true, foreground: true, now: tick, commit: commit, runID: run)!
        let bytes = try store.publish(grant)
        precondition(store.matches(bytes))
        let object = try JSONSerialization.jsonObject(with: bytes) as! [String: Any]
        precondition(Set(object.keys) == ["schema", "id", "token", "created", "expires", "tools_version", "source_commit", "actions_run_id"])
        var info = stat()
        precondition(lstat(directory.path, &info) == 0 && info.st_uid == getuid() && (info.st_mode & 0o7777) == 0o700)
        let file = directory.appendingPathComponent("session.json")
        precondition(lstat(file.path, &info) == 0 && info.st_uid == getuid() && (info.st_mode & 0o7777) == 0o600 && info.st_nlink == 1)
        precondition(!store.matches(bytes + Data([0])))
        precondition(chmod(file.path, 0o644) == 0 && !store.matches(bytes))
        expectPolicyError { _ = try store.publish(grant) }
        precondition(chmod(file.path, 0o600) == 0)
        let hardlink = root.appendingPathComponent("linked-session")
        precondition(link(file.path, hardlink.path) == 0 && !store.matches(bytes))
        expectPolicyError { try store.remove() }
        precondition(unlink(hardlink.path) == 0)
        try store.remove()
        precondition(!FileManager.default.fileExists(atPath: file.path) && !store.matches(bytes))
        let target = root.appendingPathComponent("other-file")
        try bytes.write(to: target)
        precondition(symlink(target.path, file.path) == 0 && !store.matches(bytes))
        expectPolicyError { _ = try store.publish(grant) }
        expectPolicyError { try store.remove() }
        precondition(unlink(file.path) == 0)
        precondition(chmod(directory.path, 0o755) == 0)
        expectPolicyError { _ = try store.publish(grant) }
        precondition(chmod(directory.path, 0o700) == 0)
        let directoryLink = root.appendingPathComponent("linked-directory")
        precondition(symlink(directory.path, directoryLink.path) == 0)
        let linkedStore = AutomationSessionFile(directory: directoryLink.path, owner: getuid())
        expectPolicyError { _ = try linkedStore.publish(grant) }
    }

    private static func requireAbsent(_ session: AutomationSession?) { precondition(session == nil) }

    private static func expectPolicyError(_ operation: () throws -> Void) {
        do {
            try operation()
            preconditionFailure("unsafe session file or directory must be refused")
        } catch AutomationSessionFailure.filePolicy {} catch {
            preconditionFailure("unexpected session error")
        }
    }
}
