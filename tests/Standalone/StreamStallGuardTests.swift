// Only scripts/test_stream_stall_guard.py supplies the production guard.
// No production body is copied here. The upstream streams are real
// AsyncThrowingStreams driven from a disposable CLI process; timing windows
// are deliberately generous because this runs on shared CI machines.
// Swift 5 language mode, explicit macOS SDK/target.
import Foundation
#if canImport(Darwin)
import Darwin
#endif

enum StallTestError: Error, Equatable {
    case stall
    case upstream
}

final class LockedCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0
    func increment() {
        lock.lock()
        value += 1
        lock.unlock()
    }
    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

final class LockedBox<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var value: T?
    func set(_ newValue: T) {
        lock.lock()
        value = newValue
        lock.unlock()
    }
    var current: T? {
        lock.lock()
        defer { lock.unlock() }
        return value
    }
}

/// Test-owned producer handle: the test holds the continuation so a stream can
/// stay open (and silent) for as long as a case needs.
final class Upstream<Element: Sendable>: @unchecked Sendable {
    let stream: AsyncThrowingStream<Element, Error>
    let cont: AsyncThrowingStream<Element, Error>.Continuation
    init() {
        var captured: AsyncThrowingStream<Element, Error>.Continuation!
        stream = AsyncThrowingStream { captured = $0 }
        cont = captured
    }
}

private final class Checks {
    var pass = 0
    var fail = 0
    func expect(_ id: String, _ condition: Bool) {
        if condition { pass += 1; print("PASS[\(id)]") }
        else { fail += 1; print("FAIL[\(id)]") }
    }
}

private func passChecks(_ h: Checks) async {
    let stallCount = LockedCounter()
    let up = Upstream<Int>()
    let guarded = StreamStallGuard.wrap(up.stream, stallTimeout: 5.0, pollInterval: 0.05,
                                        onStall: { _ in stallCount.increment(); return StallTestError.stall })
    let producer = Task {
        for i in 0..<8 {
            try? await Task.sleep(nanoseconds: 20_000_000)
            up.cont.yield(i)
        }
        up.cont.finish()
    }
    var got: [Int] = []
    var threw: Error?
    var it = guarded.makeAsyncIterator()
    do {
        while true {
            guard let element = try await it.next() else { break }
            got.append(element)
        }
    } catch { threw = error }
    _ = await producer.value
    try? await Task.sleep(nanoseconds: 300_000_000)
    h.expect("pass.events_ordered", got == Array(0..<8))
    h.expect("pass.clean_end", threw == nil)
    h.expect("pass.no_stall", stallCount.count == 0)
}

private func burstChecks(_ h: Checks) async {
    let stallCount = LockedCounter()
    let up = Upstream<Int>()
    let guarded = StreamStallGuard.wrap(up.stream, stallTimeout: 5.0, pollInterval: 0.05,
                                        onStall: { _ in stallCount.increment(); return StallTestError.stall })
    let producer = Task {
        for i in 0..<200 { up.cont.yield(i) }
        up.cont.finish()
    }
    var got: [Int] = []
    var threw: Error?
    var it = guarded.makeAsyncIterator()
    do {
        while true {
            guard let element = try await it.next() else { break }
            got.append(element)
        }
    } catch { threw = error }
    _ = await producer.value
    h.expect("burst.count", got.count == 200)
    h.expect("burst.ordered", got == Array(0..<200))
    h.expect("burst.clean_end", threw == nil)
}

private func stallChecks(_ h: Checks) async {
    let stallCount = LockedCounter()
    let fireBox = LockedBox<StreamStallGuard.Fire>()
    let up = Upstream<Int>()
    let guarded = StreamStallGuard.wrap(up.stream, stallTimeout: 0.4, pollInterval: 0.05,
                                        onStall: { fire in
                                            stallCount.increment()
                                            fireBox.set(fire)
                                            return StallTestError.stall
                                        })
    up.cont.yield(1)
    let started = Date()
    var got: [Int] = []
    var threw: Error?
    var elapsed: TimeInterval = 0
    var it = guarded.makeAsyncIterator()
    do {
        while true {
            guard let element = try await it.next() else { break }
            got.append(element)
        }
    } catch {
        threw = error
        elapsed = Date().timeIntervalSince(started)
    }
    try? await Task.sleep(nanoseconds: 300_000_000)
    let fire = fireBox.current
    h.expect("stall.first_event", got == [1])
    h.expect("stall.error_is_stall", (threw as? StallTestError) == .stall)
    h.expect("stall.fired_once", stallCount.count == 1)
    h.expect("stall.timing", elapsed >= 0.35 && elapsed <= 3.0)
    h.expect("stall.snapshot_fields", fire != nil && fire?.nominal == 0.4 && fire?.eventsThisStream == 1)
    h.expect("stall.no_second_fire", stallCount.count == 1)
}

private func upstreamChecks(_ h: Checks) async {
    let stallCount = LockedCounter()
    let up = Upstream<Int>()
    let guarded = StreamStallGuard.wrap(up.stream, stallTimeout: 5.0, pollInterval: 0.05,
                                        onStall: { _ in stallCount.increment(); return StallTestError.stall })
    let producer = Task {
        up.cont.yield(1)
        try? await Task.sleep(nanoseconds: 30_000_000)
        up.cont.yield(2)
        try? await Task.sleep(nanoseconds: 30_000_000)
        up.cont.finish(throwing: StallTestError.upstream)
    }
    var got: [Int] = []
    var threw: Error?
    var it = guarded.makeAsyncIterator()
    do {
        while true {
            guard let element = try await it.next() else { break }
            got.append(element)
        }
    } catch { threw = error }
    _ = await producer.value
    try? await Task.sleep(nanoseconds: 300_000_000)
    h.expect("upstream.events", got == [1, 2])
    h.expect("upstream.error_is_upstream", (threw as? StallTestError) == .upstream)
    h.expect("upstream.no_stall", stallCount.count == 0)
}

private func cancelChecks(_ h: Checks) async {
    let stallCount = LockedCounter()
    let upstreamTerminated = LockedCounter()
    let up = Upstream<Int>()
    up.cont.onTermination = { _ in upstreamTerminated.increment() }
    let guarded = StreamStallGuard.wrap(up.stream, stallTimeout: 1.2, pollInterval: 0.05,
                                        onStall: { _ in stallCount.increment(); return StallTestError.stall })
    up.cont.yield(1)
    let consumer = Task { () -> String in
        var it = guarded.makeAsyncIterator()
        do {
            guard let first = try await it.next(), first == 1 else { return "first-bad" }
            let second = try await it.next()
            return second == nil ? "nil" : "value"
        } catch {
            return "threw"
        }
    }
    try? await Task.sleep(nanoseconds: 150_000_000)
    consumer.cancel()
    let outcome = await consumer.value
    var terminated = false
    for _ in 0..<40 {
        if upstreamTerminated.count >= 1 { terminated = true; break }
        try? await Task.sleep(nanoseconds: 50_000_000)
    }
    try? await Task.sleep(nanoseconds: 300_000_000)
    h.expect("cancel.consumer_resumed", outcome == "nil" || outcome == "threw")
    h.expect("cancel.upstream_terminated", terminated)
    h.expect("cancel.no_stall", stallCount.count == 0)
}

@main struct StreamStallGuardMain {
    static func main() async {
        let mode = CommandLine.arguments.dropFirst().first ?? "pass"
        let h = Checks()
        switch mode {
        case "pass": await passChecks(h)
        case "burst": await burstChecks(h)
        case "stall": await stallChecks(h)
        case "upstream": await upstreamChecks(h)
        case "cancel": await cancelChecks(h)
        default:
            print("invalid case")
            exit(2)
        }
        print("SUMMARY case=\(mode) pass=\(h.pass) fail=\(h.fail) skip=0")
        exit(h.fail == 0 ? 0 : 1)
    }
}
