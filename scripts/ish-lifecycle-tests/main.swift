import Foundation
import Dispatch

// Standalone native macOS regression harness; no app, kernel, or main run loop.
private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError("FAIL: \(message)") }
}

private func wait(_ semaphore: DispatchSemaphore, _ message: String,
                  seconds: TimeInterval = 3) {
    expect(semaphore.wait(timeout: .now() + seconds) == .success, message)
}

private final class Probe: @unchecked Sendable {
    struct Snapshot {
        var outcomes: [ISHCommandLifecycle.Outcome] = []
        var pids: [Int32] = []
        var reasons: [Bool] = []
        var lines: [String] = []
    }
    private let lock = NSLock()
    private var value = Snapshot()
    let done = DispatchSemaphore(value: 0)
    let cleaned = DispatchSemaphore(value: 0)
    let pidEvent = DispatchSemaphore(value: 0)
    let cleared = DispatchSemaphore(value: 0)
    let lineEvent = DispatchSemaphore(value: 0)

    var snapshot: Snapshot {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
    func completed(_ outcome: ISHCommandLifecycle.Outcome) {
        lock.lock(); value.outcomes.append(outcome); lock.unlock()
        done.signal()
    }
    func pid(_ pid: Int32) {
        lock.lock(); value.pids.append(pid); lock.unlock()
        pidEvent.signal()
        if pid == 0 { cleared.signal() }
    }
    func cleanup(_ timedOut: Bool) {
        lock.lock(); value.reasons.append(timedOut); lock.unlock()
        cleaned.signal()
    }
    func line(_ line: String) {
        lock.lock(); value.lines.append(line); lock.unlock()
        lineEvent.signal()
    }
}

private func make(_ timeout: TimeInterval = 10, cap: Int = 64,
                  bind: Bool = true) -> (ISHCommandLifecycle, Probe) {
    let probe = Probe()
    let lifecycle = ISHCommandLifecycle(timeout: timeout, maxPartialChars: cap,
        onPID: { probe.pid($0) }, requestCleanup: { probe.cleanup($0) })
    if bind { lifecycle.bindCompletion { probe.completed($0) } }
    return (lifecycle, probe)
}

private func checkInterrupted(_ output: String, timeout: Bool) {
    expect(output.contains(timeout ? "Command timed out" : "Command cancelled"),
           "correct terminal reason")
    expect(output.contains("cleanup requested; process termination is not confirmed"),
           "never claim process termination")
    expect(!output.lowercased().contains("killed"), "no false kill claim")
}

private func testNormalAndFormatting() {
    let (lifecycle, p) = make()
    lifecycle.armDeadline()
    lifecycle.publishPID(42)
    wait(p.pidEvent, "positive PID")
    lifecycle.receiveLine("live") { p.line($0) }
    wait(p.lineEvent, "live line")
    lifecycle.finish(exitCode: 7, stdout: "out", stderr: "err")
    wait(p.done, "normal completion")
    wait(p.cleared, "normal completion must publish PID 0")
    expect(lifecycle.isTerminal, "normal terminal")
    expect(p.snapshot.outcomes.count == 1, "normal once")
    expect(p.snapshot.outcomes[0].output == "out\nerr\n(exit code: 7)", "format compatibility")
    expect(p.snapshot.outcomes[0].exitCode == 7, "normal exit code")
    expect(!p.snapshot.outcomes[0].wasCancelled, "normal result is not Task cancellation")
    expect(p.snapshot.pids == [42, 0], "ordered positive then clear")
    lifecycle.cancel()
    lifecycle.failToLaunch("late")
    lifecycle.finish(exitCode: 0, stdout: "late", stderr: "")
    expect(p.snapshot.reasons.isEmpty, "normal exit does not request cancellation")
    expect(p.snapshot.outcomes.count == 1, "late terminal methods ignored")

    let (empty, ep) = make()
    empty.finish(exitCode: 0, stdout: " \n", stderr: "")
    expect(ep.snapshot.outcomes[0].output == "(command completed with no output)", "empty format")
    let (annotated, ap) = make()
    annotated.finish(exitCode: 1, stdout: "already exit code: 1", stderr: "")
    expect(ap.snapshot.outcomes[0].output == "already exit code: 1", "no duplicate exit annotation")
}

private func testCancelBeforeBindingAndLateTicketPID() {
    // Model the required ticket contract: cleanup intent survives absent PID.
    final class Ticket: @unchecked Sendable {
        private let lock = NSLock()
        private var requested = false
        private var pid: Int32 = 0
        private var cleanedPID: Int32 = 0
        func request() {
            lock.lock(); requested = true
            if pid > 0 { cleanedPID = pid }
            lock.unlock()
        }
        func attach(_ newPID: Int32) {
            lock.lock(); pid = newPID
            if requested { cleanedPID = newPID }
            lock.unlock()
        }
        var cleaned: Int32 {
            lock.lock(); defer { lock.unlock() }; return cleanedPID
        }
    }
    let p = Probe()
    let ticket = Ticket()
    let lifecycle = ISHCommandLifecycle(timeout: 0.02, maxPartialChars: 32,
        onPID: { p.pid($0) }, requestCleanup: { reason in
            ticket.request(); p.cleanup(reason)
        })
    lifecycle.cancel()
    lifecycle.cancel()
    lifecycle.armDeadline()
    wait(p.cleaned, "prelaunch cleanup")
    expect(p.snapshot.reasons == [false], "cancel cleanup once and reason false")
    expect(lifecycle.isTerminal, "cancel before bind is terminal")
    ticket.attach(811)
    lifecycle.publishPID(811)
    lifecycle.publishPID(812)
    expect(ticket.cleaned == 811, "original ticket cleans late attached process")
    lifecycle.bindCompletion { outcome in
        expect(lifecycle.isTerminal, "completion can reenter state")
        lifecycle.cancel()
        p.completed(outcome)
    }
    lifecycle.bindCompletion { _ in fatalError("duplicate binding delivered") }
    wait(p.done, "prebound cancel delivered")
    wait(p.cleared, "prebound clear")
    expect(p.snapshot.pids == [0], "late PID never revives ticket")
    expect(p.snapshot.outcomes.count == 1, "prebound result once")
    checkInterrupted(p.snapshot.outcomes[0].output, timeout: false)
    expect(p.snapshot.outcomes[0].wasCancelled, "user cancellation has a distinct terminal marker")
}

private func testTimeoutBeforeBinding() {
    let (lifecycle, p) = make(0.03, bind: false)
    lifecycle.armDeadline()
    lifecycle.armDeadline()
    wait(p.cleaned, "deadline before binding")
    wait(p.cleared, "deadline clears absent PID")
    lifecycle.cancel()
    lifecycle.publishPID(99)
    lifecycle.bindCompletion { p.completed($0) }
    wait(p.done, "late bind receives deadline")
    expect(p.snapshot.reasons == [true], "deadline cleanup once and reason true")
    expect(p.snapshot.pids == [0], "timeout late PID suppressed")
    expect(p.snapshot.outcomes[0].exitCode == -1, "timeout exit code")
    expect(!p.snapshot.outcomes[0].wasCancelled, "deadline is not Task cancellation")
    checkInterrupted(p.snapshot.outcomes[0].output, timeout: true)
}

private func testStrictTailAndSingleLine() {
    let (lifecycle, p) = make(0.03, cap: 8)
    lifecycle.receiveLine(String(repeating: "x", count: 200_000) + "TAIL1234") { _ in }
    lifecycle.armDeadline()
    wait(p.done, "huge line timeout")
    let output = p.snapshot.outcomes[0].output
    expect(output.contains("chars was dropped]\n\nTAIL1234\n\n[Command timed out"),
           "one oversized line keeps exactly bounded tail")
    expect(!output.contains("xxxxxxxxx"), "oversized line mirror is strictly capped")
    checkInterrupted(output, timeout: true)

    let (multi, mp) = make(cap: 8)
    multi.receiveLine("old-old") { _ in }
    multi.receiveLine("abc") { _ in }
    multi.receiveLine("def") { _ in }
    multi.cancel()
    let multiOutput = mp.snapshot.outcomes[0].output
    let expectedTail = String("old-old\nabc\ndef".suffix(8))
    expect(multiOutput.contains("chars was dropped]\n\n" + expectedTail + "\n\n[Command cancelled"),
           "multiple lines retain newest eight characters including separators")

    let (unicode, up) = make(cap: 3)
    unicode.receiveLine("a👩‍👩‍👧‍👦中🙂") { _ in }
    unicode.cancel()
    expect(up.snapshot.outcomes[0].output.contains("\n\n👩‍👩‍👧‍👦中🙂\n\n"), "Character-safe suffix")

    for cap in [0, -1] {
        let (zero, zp) = make(cap: cap)
        zero.receiveLine("SECRET") { _ in }
        zero.cancel()
        expect(!zp.snapshot.outcomes[0].output.contains("SECRET"), "nonpositive capacity retains nothing")
    }
}

private func testBlockedDeliveryDoesNotBlockCancel() {
    let (lifecycle, p) = make()
    let entered = DispatchSemaphore(value: 0)
    let release = DispatchSemaphore(value: 0)
    lifecycle.receiveLine("started") { line in
        entered.signal()
        wait(release, "release already-started line")
        p.line(line)
    }
    wait(entered, "line callback started")
    lifecycle.publishPID(123)
    lifecycle.receiveLine("queued") { p.line($0) }
    DispatchQueue.global(qos: .utility).async { lifecycle.cancel() }
    wait(p.done, "cancel bypasses blocked delivery")
    wait(p.cleaned, "cleanup bypasses blocked delivery")
    expect(p.snapshot.pids.isEmpty, "clear remains ordered behind started callback")
    release.signal()
    wait(p.cleared, "clear after callback releases")
    expect(p.snapshot.pids == [0], "queued positive PID suppressed")
    expect(p.snapshot.lines == ["started"], "queued line suppressed")
    lifecycle.receiveLine("late") { p.line($0) }
    lifecycle.publishPID(444)
    expect(p.snapshot.lines == ["started"], "post-terminal line ignored")
}

private func testBlockedPIDDoesNotBlockDeadline() {
    let p = Probe()
    let entered = DispatchSemaphore(value: 0)
    let release = DispatchSemaphore(value: 0)
    let lifecycle = ISHCommandLifecycle(timeout: 0.03, maxPartialChars: 16,
        onPID: { pid in
            if pid > 0 {
                entered.signal()
                wait(release, "release started PID callback")
            }
            p.pid(pid)
        }, requestCleanup: { p.cleanup($0) })
    lifecycle.bindCompletion { p.completed($0) }
    lifecycle.publishPID(9)
    wait(entered, "PID callback started")
    lifecycle.receiveLine("queued") { p.line($0) }
    lifecycle.publishPID(10)
    lifecycle.armDeadline()
    wait(p.done, "deadline bypasses blocked PID callback")
    expect(p.snapshot.outcomes[0].output.contains("queued"), "mirror independent of line delivery")
    release.signal()
    wait(p.cleared, "PID zero after started callback")
    expect(p.snapshot.pids == [9, 0], "started PID permitted, queued PID suppressed")
    expect(p.snapshot.lines.isEmpty, "queued line dropped after deadline")
}

private func testBlockingCleanupAndIndependentDeadlines() {
    for timedOut in [false, true] {
        let p = Probe()
        let entered = DispatchSemaphore(value: 0)
        let release = DispatchSemaphore(value: 0)
        let cleanupFinished = DispatchSemaphore(value: 0)
        let lifecycle = ISHCommandLifecycle(timeout: 0.03, maxPartialChars: 8,
            onPID: { p.pid($0) }, requestCleanup: { reason in
                p.cleanup(reason)
                entered.signal()
                wait(release, "release blocked cleanup")
                cleanupFinished.signal()
            })
        lifecycle.bindCompletion { p.completed($0) }
        if timedOut {
            lifecycle.armDeadline()
        } else {
            DispatchQueue.global(qos: .utility).async { lifecycle.cancel() }
        }
        wait(entered, "cleanup has actually entered")
        wait(p.done, "Stop/deadline cannot wait for cleanup completion")
        expect(p.snapshot.reasons == [timedOut], "blocked cleanup has winning cause")
        // A separate controller must expire while the first cleanup is stuck.
        let (other, op) = make(0.03)
        other.armDeadline()
        wait(op.done, "second controller independent deadline")
        wait(op.cleaned, "second controller independent cleanup")
        expect(other.isTerminal && lifecycle.isTerminal, "both waits terminal")
        lifecycle.cancel()
        lifecycle.publishPID(700)
        lifecycle.publishPID(701)
        expect(p.snapshot.reasons == [timedOut], "late PID/double stop do not repeat cleanup")
        release.signal()
        wait(cleanupFinished, "cleanup eventually exits")
    }
}

private func testBlockedCompletionDoesNotSerializeControllers() {
    let entered = DispatchSemaphore(value: 0)
    let release = DispatchSemaphore(value: 0)
    let finished = DispatchSemaphore(value: 0)
    let (first, _) = make(0.02, bind: false)
    first.bindCompletion { _ in
        entered.signal()
        wait(release, "release blocked completion")
        finished.signal()
    }
    first.armDeadline()
    wait(entered, "first deadline completion started")
    let (second, sp) = make(0.02)
    second.armDeadline()
    wait(sp.done, "global utility deadline not shared serial delivery")
    release.signal()
    wait(finished, "first completion released")
}

private func testReentrantCleanupAndLaunchFailure() {
    let p = Probe()
    // The cleanup closure reenters via a reference assigned before any event.
    final class Reference: @unchecked Sendable { var lifecycle: ISHCommandLifecycle? }
    let reference = Reference()
    let lifecycle = ISHCommandLifecycle(timeout: 10, maxPartialChars: 8,
        onPID: { p.pid($0) }, requestCleanup: { reason in
            expect(reference.lifecycle!.isTerminal, "cleanup outside lock")
            reference.lifecycle!.cancel()
            p.cleanup(reason)
        })
    reference.lifecycle = lifecycle
    lifecycle.bindCompletion { outcome in
        expect(lifecycle.isTerminal, "completion outside lock")
        lifecycle.cancel()
        p.completed(outcome)
    }
    lifecycle.failToLaunch("Failed to create process")
    wait(p.done, "launch failure completed")
    wait(p.cleaned, "launch failure cleanup")
    wait(p.cleared, "launch failure clear")
    expect(p.snapshot.outcomes[0].output == "Error: Failed to create process", "launch error format")
    expect(p.snapshot.outcomes[0].exitCode == -1, "launch failure code")
    expect(!p.snapshot.outcomes[0].wasCancelled, "launch failure is not Task cancellation")
    expect(p.snapshot.reasons == [false], "launch failure is not a deadline")
    reference.lifecycle = nil
}

private func testNormalFinishThenLatePID() {
    let (lifecycle, p) = make()
    lifecycle.finish(exitCode: 0, stdout: "ok", stderr: "")
    lifecycle.publishPID(88)
    lifecycle.publishPID(89)
    lifecycle.cancel()
    wait(p.cleared, "late PID never emits positive")
    expect(p.cleaned.wait(timeout: .now() + 0.03) == .timedOut,
           "normal completion must preserve intentional background children")
    expect(p.snapshot.reasons.isEmpty, "normal finish and late PID never request cleanup")
    expect(p.snapshot.pids == [0], "normal finish cannot revive PID")
    expect(p.snapshot.outcomes.count == 1, "late events cannot repeat completion")
    expect(p.snapshot.outcomes[0].output == "ok", "late events cannot replace original result")
}

private func testTerminalRaces() {
    // Alternate scheduling bias to exercise both winners, and also force the
    // deterministic early-finish/early-deadline paths in the preceding tests.
    for index in 0..<60 {
        let (lifecycle, p) = make(0.003)
        let competitors = DispatchGroup()
        lifecycle.armDeadline()
        competitors.enter()
        DispatchQueue.global(qos: .utility).asyncAfter(
            deadline: .now() + (index % 2 == 0 ? 0 : 0.004)) {
            lifecycle.finish(exitCode: 0, stdout: "finished", stderr: "")
            competitors.leave()
        }
        wait(p.done, "normal/timeout race resolves")
        expect(competitors.wait(timeout: .now() + 3) == .success, "race competitor exits")
        wait(p.cleared, "race PID clear")
        lifecycle.cancel()
        lifecycle.cancel()
        lifecycle.bindCompletion { _ in fatalError("race delivered to second binding") }
        expect(p.snapshot.outcomes.count == 1, "race completion exactly once")
        expect(p.snapshot.pids == [0], "race terminal clear exactly once")
        if p.snapshot.outcomes[0].exitCode == -1 {
            expect(!p.snapshot.outcomes[0].wasCancelled, "deadline race winner is timeout, not Task cancel")
            wait(p.cleaned, "winning timeout requests cleanup")
            expect(p.snapshot.reasons == [true], "only winning timeout cleanup")
        } else {
            expect(!p.snapshot.outcomes[0].wasCancelled, "normal completion is not cancellation")
            expect(p.snapshot.outcomes[0].output == "finished", "normal winner preserved")
            expect(p.snapshot.reasons.isEmpty, "losing timeout cannot request cleanup")
        }
    }
    let (lifecycle, p) = make()
    let group = DispatchGroup()
    for _ in 0..<24 {
        group.enter()
        DispatchQueue.global(qos: .utility).async {
            lifecycle.cancel()
            group.leave()
        }
    }
    expect(group.wait(timeout: .now() + 3) == .success, "concurrent cancels return")
    wait(p.done, "concurrent cancel terminal")
    wait(p.cleaned, "concurrent cancel cleanup")
    wait(p.cleared, "concurrent cancel clear")
    expect(p.snapshot.outcomes.count == 1, "concurrent cancel once")
    expect(p.snapshot.outcomes[0].wasCancelled, "concurrent Task-independent cancel is marked cancelled")
    expect(p.snapshot.reasons == [false], "concurrent cancel one cleanup")
    expect(p.snapshot.pids == [0], "concurrent cancel one clear")
}

private let tests: [(String, () -> Void)] = [
    ("normal completion and formatting", testNormalAndFormatting),
    ("cancel before binding and late ticket PID", testCancelBeforeBindingAndLateTicketPID),
    ("timeout before binding and idempotent arm", testTimeoutBeforeBinding),
    ("strict tail including huge single line and Unicode", testStrictTailAndSingleLine),
    ("blocked line delivery cannot delay cancel", testBlockedDeliveryDoesNotBlockCancel),
    ("blocked PID delivery cannot delay deadline", testBlockedPIDDoesNotBlockDeadline),
    ("blocked cleanup and independent deadlines", testBlockingCleanupAndIndependentDeadlines),
    ("blocked completion cannot serialize controllers", testBlockedCompletionDoesNotSerializeControllers),
    ("reentrant callbacks and launch failure", testReentrantCleanupAndLaunchFailure),
    ("normal finish then late PID", testNormalFinishThenLatePID),
    ("normal/timeout races and concurrent cancels", testTerminalRaces)
]
for (name, test) in tests {
    test()
    print("PASS: \(name)")
}
print("PASS: \(tests.count) native ISHCommandLifecycle regression groups")
