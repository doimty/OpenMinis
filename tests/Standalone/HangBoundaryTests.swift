// Only scripts/test_ios15_hang_boundaries.py supplies the production methods.
// No production body is copied here. Dependencies and snapshot application are
// spies, NOT UIKit. Foundation notification delivery is real, macOS-only.
// Run on a disposable CLI process; its main queue is held for at most 250ms,
// never on an iPhone. Swift 5 language mode, explicit macOS SDK/target.
import Foundation
import Dispatch
#if canImport(Darwin)
import Darwin
#endif

struct BoundaryMessage: Equatable {
    enum Role { case assistant, user }
    var role: Role = .assistant
    var blocks: [String]
}

@MainActor final class BoundaryVM {
    var messages = [BoundaryMessage(blocks: ["initial"])]
    var isLoadingSession = false
    var streamingUIUpdatesSuspended = false
    var isProcessing = true
}

struct AppLogger {
    let category: String
    func info(_ message: String) {} // Log formatting is compiled, no app logger.
}

extension Notification.Name {
    // Test-only names isolate the default Foundation center from host events.
    static let sessionAgentLoopDidEnd = Notification.Name("HangBoundary.loopEnd")
    static let sessionDidUpdate = Notification.Name("HangBoundary.activity")
}

@MainActor final class BoundaryRecorder {
    var sessionIds: [String] = []
    var activityCount = 0
    var mainThreadFlags: [Bool] = []
}

@MainActor private final class Checks {
    var pass = 0
    var fail = 0
    func expect(_ id: String, _ condition: Bool) {
        if condition { pass += 1; print("PASS[\(id)]") }
        else { fail += 1; print("FAIL[\(id)]") }
    }
}

@MainActor private func pendingChecks(_ h: Checks) {
    let vm = BoundaryVM()
    let c = BoundaryCoordinator()
    c.hasPendingSnapshot = true
    c.flush("nil-vm")
    h.expect("pending.nil_vm_preserved", c.hasPendingSnapshot && c.attempts == 0)
    c.vm = vm
    c.flush("vm-restored")
    h.expect("pending.nil_vm_recovers", !c.hasPendingSnapshot && c.applied.count == 1)

    c.reset()
    c.hasPendingSnapshot = true
    vm.isLoadingSession = true
    c.flush("load-backstop")
    h.expect("pending.loading_preserved", c.hasPendingSnapshot)
    h.expect("pending.loading_no_apply", c.attempts == 0 && c.applied.isEmpty)
    vm.messages = [BoundaryMessage(blocks: ["loaded", "latest"])]
    vm.isLoadingSession = false
    c.flush("load-complete")
    h.expect("pending.loading_recovers", !c.hasPendingSnapshot && c.applied == [vm.messages])

    c.reset()
    c.hasPendingSnapshot = true
    vm.streamingUIUpdatesSuspended = true
    c.flush("tail-while-suspended")
    h.expect("pending.suspended_preserved", c.hasPendingSnapshot)
    h.expect("pending.suspended_no_apply", c.attempts == 0 && c.applied.isEmpty)
    vm.messages = [BoundaryMessage(blocks: ["new-tail", "tool", "final"])]
    vm.streamingUIUpdatesSuspended = false
    vm.isProcessing = true // Foreground resume may occur mid-agent-loop.
    c.flush("suspend-resume")
    h.expect("pending.processing_resume", !c.hasPendingSnapshot && c.applied.count == 1)
    h.expect("pending.latest_messages", c.applied == [vm.messages])
    c.flush("duplicate-resume")
    c.flush("settle")
    h.expect("pending.once", c.attempts == 1 && c.applied.count == 1)

    c.reset()
    c.hasPendingSnapshot = false
    c.flush("no-pending")
    h.expect("pending.no_pending_no_apply", c.attempts == 0 && c.applied.isEmpty)
    for trigger in ["tail", "stop", "cancel"] {
        c.reset()
        c.hasPendingSnapshot = true
        vm.isProcessing = false
        vm.messages = [BoundaryMessage(blocks: [trigger, "terminal"])]
        c.flush(trigger)
        c.flush("terminal-replay")
        h.expect("pending.\(trigger)_consumed", !c.hasPendingSnapshot && c.applied == [vm.messages] && c.attempts == 1)
    }

    c.reset()
    c.hasPendingSnapshot = true
    vm.isLoadingSession = true
    vm.streamingUIUpdatesSuspended = true
    for n in 0..<6 {
        vm.messages = [BoundaryMessage(blocks: ["replay-\(n)"])]
        c.flush("overlap-replay")
    }
    vm.isLoadingSession = false
    c.flush("load-ended-still-suspended")
    vm.isLoadingSession = true
    vm.streamingUIUpdatesSuspended = false
    c.flush("resumed-still-loading")
    h.expect("pending.replay_preserved", c.hasPendingSnapshot && c.attempts == 0)
    vm.messages = [BoundaryMessage(blocks: ["authoritative", "last-replay"])]
    vm.isLoadingSession = false
    vm.isProcessing = true
    c.flush("both-ready")
    c.flush("replayed-again")
    h.expect("pending.replay_latest_once", !c.hasPendingSnapshot && c.applied == [vm.messages] && c.attempts == 1)

    c.reset()
    c.hasPendingSnapshot = true
    c.vm = nil
    c.flush("rebind-gap")
    let replacement = BoundaryVM()
    replacement.messages = [BoundaryMessage(blocks: ["replacement-session"])]
    c.vm = replacement
    c.flush("rebound")
    h.expect("pending.rebind_latest", !c.hasPendingSnapshot && c.applied == [replacement.messages] && c.attempts == 1)
}

@MainActor private func waitUntil(_ condition: () -> Bool) async -> Bool {
    let deadline = ProcessInfo.processInfo.systemUptime + 1.5
    while !condition() && ProcessInfo.processInfo.systemUptime < deadline {
        try? await Task.sleep(nanoseconds: 10_000_000)
    }
    return condition()
}

@MainActor private func postWhileMainHeld(_ names: [Notification.Name], object: Any) -> Bool {
    let started = DispatchSemaphore(value: 0)
    let returned = DispatchSemaphore(value: 0)
    DispatchQueue.global(qos: .userInitiated).async {
        started.signal()
        for name in names { NotificationCenter.default.post(name: name, object: object) }
        returned.signal()
    }
    guard started.wait(timeout: .now() + .milliseconds(500)) == .success else { return false }
    // Deliberately hold the CLI's main queue. queue:.main old logic yields a
    // named failure, then releases main to drain; no crash/timeout is a PASS.
    return returned.wait(timeout: .now() + .milliseconds(250)) == .success
}

@MainActor private func notificationChecks(_ h: Checks, activity: Bool) async {
    let registry = AgentJobRegistry()
    let recorder = BoundaryRecorder()
    registry.recorder = recorder
    let label = activity ? "activity" : "loop_end"
    let name: Notification.Name = activity ? .sessionDidUpdate : .sessionAgentLoopDidEnd
    let returned = postWhileMainHeld([name], object: "fixture-session")
    h.expect("notification.\(label).post_returned_while_main_held", returned)
    let delivered = await waitUntil { activity ? recorder.activityCount >= 1 : recorder.sessionIds.count >= 1 }
    h.expect("notification.\(label).eventually_delivered", delivered)
    h.expect("notification.\(label).actor_on_main", recorder.mainThreadFlags == [true])
    if !activity {
        _ = postWhileMainHeld([name], object: NSNumber(value: 7))
    }
    try? await Task.sleep(nanoseconds: 80_000_000)
    h.expect("notification.\(label).exactly_once", activity
        ? recorder.activityCount == 1 && recorder.sessionIds.isEmpty
        : recorder.sessionIds == ["fixture-session"] && recorder.activityCount == 0)
    withExtendedLifetime(registry) {}
}

@MainActor private func weakChecks(_ h: Checks) async {
    let recorder = BoundaryRecorder()
    var registry: AgentJobRegistry? = AgentJobRegistry()
    registry?.recorder = recorder
    weak var weakRegistry = registry
    let returned = postWhileMainHeld([.sessionAgentLoopDidEnd, .sessionDidUpdate], object: "release-before-drain")
    h.expect("weak.post_returned", returned)
    registry = nil // Both Task closures were queued, but main has not yielded.
    h.expect("weak.released_before_actor_drain", weakRegistry == nil)
    try? await Task.sleep(nanoseconds: 100_000_000)
    h.expect("weak.no_callback_after_release", weakRegistry == nil && recorder.sessionIds.isEmpty && recorder.activityCount == 0)
}

@main struct HangBoundaryMain {
    @MainActor static func main() async {
        let mode = CommandLine.arguments.dropFirst().first ?? "pending"
        let h = Checks()
        switch mode {
        case "pending": pendingChecks(h)
        case "loop_end": await notificationChecks(h, activity: false)
        case "activity": await notificationChecks(h, activity: true)
        case "weak": await weakChecks(h)
        default: print("invalid case"); exit(2)
        }
        print("SUMMARY case=\(mode) pass=\(h.pass) fail=\(h.fail) skip=0")
        exit(h.fail == 0 ? 0 : 1)
    }
}
