import Foundation
import Dispatch

/// One execution ticket's wait lifecycle; deliberately independent of the kernel/UI.
///
/// `requestCleanup` MUST target the original execution ticket, not a reusable PID.
/// It must remember cancellation even before launch/PID publication, and arrange
/// cleanup of a subsequently attached process. This object requests cleanup at
/// most once; the ticket's cleanup implementation must itself be idempotent.
/// Its Bool is true only for a winning deadline; cancel and launch failure pass
/// false. Normal completion (including late positive PID publication) never
/// requests cleanup: commands may intentionally leave background children alive.
/// Terminal means the caller's wait ended, NOT that the process has terminated.
///
/// PID/line callbacks use a private serial queue. Delivery is considered started
/// when admitted under the state lock; already-started callbacks cannot be
/// interrupted and may finish after terminal. Queued positive PIDs/lines are
/// suppressed after terminal; the final PID 0 follows already-started delivery.
/// Completion runs on the winning caller/deadline thread (or the binding thread
/// for an earlier terminal outcome), never behind delivery or cleanup. Callbacks
/// may reenter this object; completion clients should promptly hand off work.
/// The character cap bounds only the partial-output mirror, not raw delivery:
/// raw line callbacks retain their original payloads and a blocked consumer can
/// build an unbounded queue. Upstream flow control is the caller's responsibility.
final class ISHCommandLifecycle: @unchecked Sendable {
    struct Outcome {
        let output: String
        let exitCode: Int
        let wasCancelled: Bool
    }

    private let lock = NSLock()
    private let timeout: TimeInterval
    private let maxPartialChars: Int
    private let onPID: (Int32) -> Void
    private let requestCleanup: (Bool) -> Void
    private let deliveryQueue = DispatchQueue(
        label: "com.openminis.ish.lifecycle.delivery", qos: .utility)
    private let cleanupQueue = DispatchQueue(
        label: "com.openminis.ish.lifecycle.cleanup", qos: .utility)

    // Every mutable field below is protected by lock.
    private var terminalOutcome: Outcome?
    private var completion: ((Outcome) -> Void)?
    private var completionBound = false
    private var deadlineArmed = false
    private var deadlineWork: DispatchWorkItem?
    private var cleanupRequested = false
    private var partialTail = ""
    private var hasLine = false
    private var partialTruncated = false

    init(timeout: TimeInterval, maxPartialChars: Int,
         onPID: @escaping (Int32) -> Void,
         requestCleanup: @escaping (Bool) -> Void) {
        // Avoid invalid/overflowing DispatchTime intervals. Invalid values expire
        // immediately; extremely large finite intervals saturate at ~68 years.
        self.timeout = timeout.isFinite ? min(max(0, timeout), TimeInterval(Int32.max)) : 0
        self.maxPartialChars = max(0, maxPartialChars)
        self.onPID = onPID
        self.requestCleanup = requestCleanup
    }

    var isTerminal: Bool {
        lock.lock()
        defer { lock.unlock() }
        return terminalOutcome != nil
    }

    /// The first binding owns the result, including cancellation before binding.
    /// Rebinding is ignored rather than delivering an outcome a second time.
    func bindCompletion(_ callback: @escaping (Outcome) -> Void) {
        lock.lock()
        guard !completionBound else { lock.unlock(); return }
        completionBound = true
        let outcome = terminalOutcome
        if outcome == nil { completion = callback }
        lock.unlock()
        if let outcome = outcome { callback(outcome) }
    }

    /// Idempotent; the deadline starts here, even if no PID/completion is bound.
    /// Never scheduled on a serial kernel-kill, cleanup, or client delivery queue.
    func armDeadline() {
        lock.lock()
        guard terminalOutcome == nil, !deadlineArmed else { lock.unlock(); return }
        deadlineArmed = true
        let work = DispatchWorkItem { [weak self] in self?.interrupt(timedOut: true) }
        deadlineWork = work
        DispatchQueue.global(qos: .utility).asyncAfter(deadline: .now() + timeout, execute: work)
        lock.unlock()
    }

    func publishPID(_ pid: Int32) {
        guard pid > 0 else { return } // Only terminal transitions publish PID 0.
        lock.lock()
        guard terminalOutcome == nil else {
            // Normal exit may intentionally leave background children alive.
            // Interrupted tickets already remember cleanup before a late launch.
            lock.unlock()
            return
        }
        deliveryQueue.async { [self] in
            guard admitDelivery() else { return }
            onPID(pid)
        }
        lock.unlock()
    }

    func receiveLine(_ line: String, deliver: @escaping (String) -> Void) {
        guard !isTerminal else { return }
        // Clip before taking the state lock: even a single huge line must never
        // be retained in the mirror or hold up the deadline while being scanned.
        let clipped = String(line.suffix(maxPartialChars))
        let lineWasClipped = clipped != line
        lock.lock()
        guard terminalOutcome == nil else { lock.unlock(); return }
        let candidate = partialTail + (hasLine ? "\n" : "") + clipped
        if lineWasClipped || candidate.count > maxPartialChars { partialTruncated = true }
        // If this line was clipped it alone supplies the entire retained tail.
        partialTail = lineWasClipped ? clipped : String(candidate.suffix(maxPartialChars))
        hasLine = true
        deliveryQueue.async { [self] in
            guard admitDelivery() else { return }
            deliver(line)
        }
        lock.unlock()
    }

    func finish(exitCode: Int, stdout: String, stderr: String) {
        var output = stdout
        if !stderr.isEmpty { output += (output.isEmpty ? "" : "\n") + stderr }
        if exitCode != 0 && !output.contains("exit code") {
            output += "\n(exit code: \(exitCode))"
        }
        if output.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            output = "(command completed with no output)"
        }
        end(needsCleanup: false) { Outcome(output: output, exitCode: exitCode, wasCancelled: false) }
    }

    func failToLaunch(_ message: String) {
        // Release a possibly partially-created ticket, without claiming a kill.
        end(needsCleanup: true) {
            Outcome(output: "Error: \(message)", exitCode: -1, wasCancelled: false)
        }
    }

    func cancel() { interrupt(timedOut: false) }

    private func admitDelivery() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return terminalOutcome == nil
    }

    private func enqueueCleanup(timedOut: Bool) {
        cleanupQueue.async { [self] in requestCleanup(timedOut) }
    }

    private func interrupt(timedOut: Bool) {
        end(needsCleanup: true, timedOut: timedOut) {
            let reason = timedOut
                ? "Command timed out after \(self.timeout)s" : "Command cancelled"
            let notice = "[\(reason); cleanup requested; process termination is not confirmed]"
            var output = self.partialTail
            if self.partialTruncated {
                output = "[Earlier output beyond the last \(self.maxPartialChars) chars was dropped]\n\n" + output
            }
            if !output.isEmpty { output += "\n\n" }
            return Outcome(output: output + notice, exitCode: -1, wasCancelled: !timedOut)
        }
    }

    /// makeOutcome is internal-only and reads the mirror under lock; no client
    /// code executes there. The lock linearizes all competing terminal causes.
    private func end(needsCleanup: Bool, timedOut: Bool = false, makeOutcome: () -> Outcome) {
        lock.lock()
        guard terminalOutcome == nil else { lock.unlock(); return }
        let outcome = makeOutcome()
        terminalOutcome = outcome
        partialTail = ""
        let callback = completion
        completion = nil
        let work = deadlineWork
        deadlineWork = nil
        let shouldCleanup = needsCleanup && !cleanupRequested
        if shouldCleanup { cleanupRequested = true }
        // Enqueue under lock to preserve order with earlier PID/line admission.
        // This barrier prevents the client callback from running under our lock.
        deliveryQueue.async { [self] in
            lock.lock()
            lock.unlock()
            onPID(0)
        }
        lock.unlock()

        work?.cancel()
        if shouldCleanup { enqueueCleanup(timedOut: timedOut) }
        callback?(outcome)
    }
}
