#if MINIS_ROOT_MANAGEMENT
import CoreFoundation
import Darwin
import Foundation
import Security

enum AutomationSessionFailure: Error {
    case invalidIdentity, randomUnavailable, filePolicy, fileIO
}

enum AutomationContinuousClock {
    static func now() -> Double {
        var scale = mach_timebase_info_data_t()
        guard mach_timebase_info(&scale) == KERN_SUCCESS, scale.denom != 0 else { return -1 }
        return Double(mach_continuous_time()) * Double(scale.numer) / Double(scale.denom) / 1_000_000_000
    }
}

struct AutomationSession: Codable, Equatable {
    let schema: Int
    let id: String
    let token: String
    let created: Double
    let expires: Double
    let toolsVersion: Int
    let sourceCommit: String
    let actionsRunID: String

    enum CodingKeys: String, CodingKey {
        case schema, id, token, created, expires
        case toolsVersion = "tools_version"
        case sourceCommit = "source_commit"
        case actionsRunID = "actions_run_id"
    }
}

/// The same reducer is used by the app and the Actions native regression.
/// It never restores a grant from disk and a consumed confirmation cannot be reused.
struct AutomationSessionState {
    static let duration: Double = 180
    private(set) var pending: UUID?
    private(set) var session: AutomationSession?

    mutating func requestStart(foreground: Bool) -> UUID? {
        guard foreground, pending == nil, session == nil else { return nil }
        let request = UUID()
        pending = request
        return request
    }

    mutating func confirm(_ request: UUID, allow: Bool, foreground: Bool, now: Double,
                          commit: String, runID: String,
                          entropy: () throws -> [UInt8] = AutomationSessionState.secureBytes) throws -> AutomationSession? {
        guard pending == request else { return nil }
        pending = nil
        guard allow, foreground, session == nil else { return nil }
        guard now.isFinite, now >= 0, commit.utf8.count == 40,
              commit.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              !runID.isEmpty, runID.first != "0", runID.utf8.allSatisfy({ (48...57).contains($0) }) else {
            throw AutomationSessionFailure.invalidIdentity
        }
        let random = try entropy()
        guard random.count == 32 else { throw AutomationSessionFailure.randomUnavailable }
        let grant = AutomationSession(schema: 1, id: UUID().uuidString.lowercased(),
                                      token: random.map { String(format: "%02x", $0) }.joined(),
                                      created: now, expires: now + Self.duration, toolsVersion: 1,
                                      sourceCommit: commit, actionsRunID: runID)
        session = grant
        return grant
    }

    mutating func current(at now: Double) -> AutomationSession? {
        guard let session else { return nil }
        guard now.isFinite, now >= session.created, now < session.expires else {
            self.session = nil
            return nil
        }
        return session
    }

    mutating func stop() {
        pending = nil
        session = nil
    }

    static func secureBytes() throws -> [UInt8] {
        var bytes = [UInt8](repeating: 0, count: 32)
        let status = bytes.withUnsafeMutableBytes {
            SecRandomCopyBytes(kSecRandomDefault, $0.count, $0.baseAddress!)
        }
        guard status == errSecSuccess else { throw AutomationSessionFailure.randomUnavailable }
        return bytes
    }
}

/// Only the fixed, mobile-owned control file is touched. Descriptor-relative
/// operations keep both atomic publication and removal inside the checked directory.
final class AutomationSessionFile {
    static let fixedDirectory = "/private/var/mobile/Library/OpenMinisAutomation"
    private let directoryPath: String
    private let owner: uid_t

    init(directory: String = AutomationSessionFile.fixedDirectory, owner: uid_t = 501) {
        directoryPath = directory
        self.owner = owner
    }

    private func directoryDescriptor(create: Bool) throws -> Int32 {
        guard getuid() == owner, geteuid() == owner else { throw AutomationSessionFailure.filePolicy }
        if create && mkdir(directoryPath, 0o700) != 0 && errno != EEXIST {
            throw AutomationSessionFailure.fileIO
        }
        let descriptor = open(directoryPath, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { throw AutomationSessionFailure.filePolicy }
        var info = stat()
        guard fstat(descriptor, &info) == 0, (info.st_mode & S_IFMT) == S_IFDIR,
              info.st_uid == owner, (info.st_mode & 0o7777) == 0o700 else {
            close(descriptor)
            throw AutomationSessionFailure.filePolicy
        }
        return descriptor
    }

    private func checkFile(_ descriptor: Int32, name: String) throws -> stat? {
        var info = stat()
        if fstatat(descriptor, name, &info, AT_SYMLINK_NOFOLLOW) != 0 {
            if errno == ENOENT { return nil }
            throw AutomationSessionFailure.fileIO
        }
        guard (info.st_mode & S_IFMT) == S_IFREG, info.st_uid == owner,
              info.st_nlink == 1, (info.st_mode & 0o7777) == 0o600,
              info.st_size > 0, info.st_size <= 4096 else { throw AutomationSessionFailure.filePolicy }
        return info
    }

    func publish(_ grant: AutomationSession) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(grant)
        guard data.count <= 4096 else { throw AutomationSessionFailure.filePolicy }
        let directoryFD = try directoryDescriptor(create: true)
        defer { close(directoryFD) }
        _ = try checkFile(directoryFD, name: "session.json")
        let temporary = ".session-\(UUID().uuidString.lowercased()).tmp"
        let descriptor = openat(directoryFD, temporary, O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW | O_CLOEXEC, 0o600)
        guard descriptor >= 0 else { throw AutomationSessionFailure.fileIO }
        defer { close(descriptor); unlinkat(directoryFD, temporary, 0) }
        guard fchmod(descriptor, 0o600) == 0 else { throw AutomationSessionFailure.fileIO }
        try data.withUnsafeBytes { buffer in
            var written = 0
            while written < buffer.count {
                let count = write(descriptor, buffer.baseAddress!.advanced(by: written), buffer.count - written)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { throw AutomationSessionFailure.fileIO }
                written += count
            }
        }
        guard fsync(descriptor) == 0 else { throw AutomationSessionFailure.fileIO }
        _ = try checkFile(directoryFD, name: temporary)
        _ = try checkFile(directoryFD, name: "session.json")
        guard renameat(directoryFD, temporary, directoryFD, "session.json") == 0,
              fsync(directoryFD) == 0 else { throw AutomationSessionFailure.fileIO }
        guard matches(data) else { throw AutomationSessionFailure.filePolicy }
        return data
    }

    func matches(_ expected: Data) -> Bool {
        guard let directoryFD = try? directoryDescriptor(create: false) else { return false }
        defer { close(directoryFD) }
        guard let expectedInfo = try? checkFile(directoryFD, name: "session.json"),
              expectedInfo.st_size == expected.count else { return false }
        let descriptor = openat(directoryFD, "session.json", O_RDONLY | O_NOFOLLOW | O_CLOEXEC)
        guard descriptor >= 0 else { return false }
        defer { close(descriptor) }
        var opened = stat()
        guard fstat(descriptor, &opened) == 0, opened.st_dev == expectedInfo.st_dev,
              opened.st_ino == expectedInfo.st_ino, opened.st_uid == owner,
              opened.st_nlink == 1, (opened.st_mode & S_IFMT) == S_IFREG,
              (opened.st_mode & 0o7777) == 0o600 else { return false }
        var bytes = [UInt8](repeating: 0, count: expected.count + 1)
        let count = bytes.withUnsafeMutableBytes { read(descriptor, $0.baseAddress!, $0.count) }
        guard count == expected.count, Data(bytes.prefix(count)) == expected,
              let finalInfo = try? checkFile(directoryFD, name: "session.json") else { return false }
        return finalInfo.st_dev == opened.st_dev && finalInfo.st_ino == opened.st_ino
    }

    func remove() throws {
        var directoryInfo = stat()
        if lstat(directoryPath, &directoryInfo) != 0 {
            if errno == ENOENT { return }
            throw AutomationSessionFailure.fileIO
        }
        let descriptor = try directoryDescriptor(create: false)
        defer { close(descriptor) }
        guard try checkFile(descriptor, name: "session.json") != nil else { return }
        guard unlinkat(descriptor, "session.json", 0) == 0, fsync(descriptor) == 0 else {
            throw AutomationSessionFailure.fileIO
        }
    }
}

/// The bearer is held here and in the private control file only. It is never
/// copied into a settings model, diagnostic report or synced MCP configuration.
final class AutomationSessionManager: @unchecked Sendable {
    static let shared = AutomationSessionManager()
    private let lock = NSLock()
    private var state = AutomationSessionState()
    private let file: AutomationSessionFile
    private let now: () -> Double
    private let notifier: (String) -> Void
    private var published: Data?
    private var events: [[String: Any]] = []

    private convenience init() {
        self.init(file: AutomationSessionFile(), now: AutomationContinuousClock.now, notifier: Self.notify)
    }

    init(file: AutomationSessionFile, now: @escaping () -> Double, notifier: @escaping (String) -> Void) {
        self.file = file
        self.now = now
        self.notifier = notifier
        try? file.remove()
        notifier("com.openminis.automation.stop")
    }

    func requestStart(foreground: Bool) -> UUID? {
        lock.lock(); defer { lock.unlock() }
        expireIfNeeded()
        return state.requestStart(foreground: foreground)
    }

    func confirm(_ request: UUID, allow: Bool, foreground: Bool, commit: String, runID: String) throws -> Bool {
        lock.lock(); defer { lock.unlock() }
        guard let grant = try state.confirm(request, allow: allow, foreground: foreground,
                                            now: now(), commit: commit, runID: runID) else { return false }
        do {
            published = try file.publish(grant)
            record(grant, event: "started", observed: now())
            notifier("com.openminis.automation.start")
            return true
        } catch {
            revoke(reason: "invalidated")
            throw error
        }
    }

    func stop(reason: String = "stopped") {
        lock.lock(); defer { lock.unlock() }
        revoke(reason: reason)
    }

    func clearReport() {
        lock.lock(); defer { lock.unlock() }
        expireIfNeeded()
        guard state.session == nil, state.pending == nil else { return }
        events.removeAll()
    }

    func eventSnapshot() -> [[String: Any]] {
        lock.lock(); defer { lock.unlock() }
        expireIfNeeded()
        return events
    }

    func secondsRemaining() -> Int {
        lock.lock(); defer { lock.unlock() }
        expireIfNeeded()
        guard let grant = state.session else { return 0 }
        return max(0, Int(ceil(grant.expires - now())))
    }

    func transport() -> String {
        lock.lock(); defer { lock.unlock() }
        expireIfNeeded()
        guard let grant = state.session else { return "{\"enabled\":false}" }
        let object: [String: Any] = ["enabled": true, "url": "http://127.0.0.1:8091/mcp",
                                     "headers": ["Authorization": "Bearer " + grant.token], "session": grant.id]
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]),
              let json = String(data: data, encoding: .utf8) else { return "{\"enabled\":false}" }
        return json
    }

    private func expireIfNeeded() {
        guard let grant = state.session else { return }
        let observed = now()
        if !observed.isFinite || observed < grant.created {
            revoke(reason: "invalidated")
        } else if observed >= grant.expires {
            revoke(reason: "expired")
        } else if published == nil || !file.matches(published!) {
            revoke(reason: "invalidated")
        }
    }

    private func revoke(reason: String) {
        if let grant = state.session {
            let observed = now()
            let event = observed.isFinite && observed >= grant.expires ? "expired" : reason
            record(grant, event: event, observed: observed)
        }
        state.stop()
        published = nil
        try? file.remove()
        notifier("com.openminis.automation.stop")
    }

    private func record(_ grant: AutomationSession, event: String, observed: Double) {
        guard observed.isFinite, observed >= 0 else { return }
        events.append(["session_id": grant.id, "event": event, "created": grant.created,
                       "expires": grant.expires, "observed": observed, "tools_version": grant.toolsVersion])
        if events.count > 64 { events.removeFirst(events.count - 64) }
    }

    private static func notify(_ name: String) {
        CFNotificationCenterPostNotification(CFNotificationCenterGetDarwinNotifyCenter(),
                                             CFNotificationName(rawValue: name as CFString), nil, nil, true)
    }
}

#if os(iOS)
import Combine
import UIKit

@objc(AutomationMCPBridge)
public final class AutomationMCPBridge: NSObject {
    @objc public static func transport() -> String {
        guard Bundle.main.bundleIdentifier == "com.openminis.app.root",
              RootManagementBuildIdentity(info: Bundle.main.infoDictionary ?? [:]) != nil else { return "{\"enabled\":false}" }
        return AutomationSessionManager.shared.transport()
    }
}

@MainActor
final class AutomationMCPController: NSObject, ObservableObject {
    static let shared = AutomationMCPController()
    static let serverName = "minis-automation"
    @Published private(set) var secondsRemaining = 0
    @Published private(set) var confirming = false
    @Published private(set) var errorKey: String?
    @Published private(set) var reportEventCount = 0
    private let manager = AutomationSessionManager.shared
    private let build = RootManagementBuildIdentity(info: Bundle.main.infoDictionary ?? [:])
    private weak var approval: UIAlertController?
    private var timer: Timer?
    private var backgroundTask = UIBackgroundTaskIdentifier.invalid

    var supported: Bool { build != nil && Bundle.main.bundleIdentifier == "com.openminis.app.root" }

    private override init() {
        super.init()
        NotificationCenter.default.addObserver(self, selector: #selector(refresh),
                                               name: UIApplication.willEnterForegroundNotification, object: nil)
        // Foreground changes do not grant or revoke a cross-app session.
        let ticker = Timer(timeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        RunLoop.main.add(ticker, forMode: .common)
        timer = ticker
    }

    private var foregroundWindow: UIWindow? {
        guard UIApplication.shared.applicationState == .active else { return nil }
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            .filter { $0.activationState == .foregroundActive }
        guard scenes.count == 1 else { return nil }
        return scenes[0].windows.first { $0.isKeyWindow && !$0.isHidden }
    }

    func requestStart() {
        refresh()
        guard supported, let window = foregroundWindow, var presenter = window.rootViewController,
              let request = manager.requestStart(foreground: true) else { return }
        while let child = presenter.presentedViewController { presenter = child }
        guard !(presenter is UIAlertController), !presenter.isBeingDismissed,
              presenter.viewIfLoaded?.window === window else { manager.stop(); return }
        confirming = true
        errorKey = nil
        let alert = UIAlertController(title: AppLocalized("Start automation session?"),
                                      message: AppLocalized("For 3 minutes, Minis tools can observe the full screen and UI, take screenshots, tap, swipe, type, and switch or close other apps. This stays authorized when Minis is in the background. Already completed actions cannot be undone. You can stop the session at any time."),
                                      preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: AppLocalized("Cancel"), style: .cancel) { [weak self] _ in
            self?.finish(request, allow: false, window: window)
        })
        alert.addAction(UIAlertAction(title: AppLocalized("Start for 3 minutes"), style: .default) { [weak self] _ in
            self?.finish(request, allow: true, window: window)
        })
        alert.preferredAction = alert.actions.first
        approval = alert
        presenter.present(alert, animated: true)
    }

    private func finish(_ request: UUID, allow: Bool, window: UIWindow) {
        confirming = false
        approval = nil
        guard let build else { manager.stop(); return }
        do {
            let started = try manager.confirm(request, allow: allow, foreground: supported && foregroundWindow === window,
                                              commit: build.commit, runID: build.actionsRunID)
            if started {
                backgroundTask = UIApplication.shared.beginBackgroundTask(withName: "Minis automation") { [weak self] in
                    Task { @MainActor in self?.stopForBackgroundBudget() }
                }
            }
        } catch {
            errorKey = "The automation session could not be created securely. Check the companion installation and try again."
        }
        refresh()
    }

    func stop() {
        manager.stop()
        confirming = false
        approval?.dismiss(animated: false)
        approval = nil
        refresh()
    }

    private func stopForBackgroundBudget() {
        manager.stop(reason: "background-budget-ended")
        refresh()
    }

    func clearReport() {
        manager.clearReport()
        refresh()
    }

    var report: String? {
        guard let build else { return nil }
#if targetEnvironment(simulator)
        let runtime = "simulator"
#elseif targetEnvironment(macCatalyst)
        let runtime = "mac-catalyst"
#else
        let runtime = ProcessInfo.processInfo.isiOSAppOnMac ? "ios-app-on-mac" : "physical-device"
#endif
        let object: [String: Any] = ["schema": 1, "kind": "minis-automation-session",
                                     "bundleIdentifier": Bundle.main.bundleIdentifier ?? "",
                                     "build": ["commit": build.commit, "actionsRunID": build.actionsRunID],
                                     "runtime": runtime, "osVersion": ProcessInfo.processInfo.operatingSystemVersionString,
                                     "appUID": getuid(), "appEUID": geteuid(), "events": manager.eventSnapshot()]
        guard let data = try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]) else { return nil }
        return String(data: data, encoding: .utf8)
    }

    @objc func refresh() {
        secondsRemaining = manager.secondsRemaining()
        reportEventCount = manager.eventSnapshot().count
        if secondsRemaining == 0, backgroundTask != .invalid {
            UIApplication.shared.endBackgroundTask(backgroundTask)
            backgroundTask = .invalid
        }
    }
}
#endif
#endif
