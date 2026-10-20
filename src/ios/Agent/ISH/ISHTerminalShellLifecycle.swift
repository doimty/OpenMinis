//
//  ISHTerminalShellLifecycle.swift
//  MinisApp
//
//  Launch/stop bookkeeping for the interactive terminal shell.
//

import Foundation

/// Launch/stop state for the interactive terminal's shell.
///
/// The interactive terminal is the one execution path that outlives a single
/// call: its shell keeps running until the user stops it, the kernel goes away
/// or the app exits. Everything here is pure bookkeeping, so the ordering
/// rules can be exercised on a host without a kernel:
///
/// - one launch slot per terminal view, so a repeated `onAppear` cannot stack
///   shells on top of each other;
/// - launch reports are matched by the ticket token that produced them, so a
///   late report from an abandoned launch never flips the UI back to running;
/// - stop claims the token exactly once and is idempotent afterwards.
struct ISHTerminalShellLifecycle {
    enum Phase: Equatable {
        case idle
        case launching(token: UInt64)
        case running(token: UInt64, pid: Int32)
        case stopped
    }

    private(set) var phase: Phase = .idle

    /// True between the first launch attempt and the stop/failure that ends it.
    var isActive: Bool {
        switch phase {
        case .launching, .running:
            return true
        case .idle, .stopped:
            return false
        }
    }

    /// Reserves the launch slot. Returns false when a shell is already
    /// launching or running.
    mutating func beginLaunch(token: UInt64) -> Bool {
        guard token != 0, !isActive else { return false }
        phase = .launching(token: token)
        return true
    }

    /// Applies the kernel's launch report. A non-positive PID means no guest
    /// process is running for that token. Returns false for a report that does
    /// not belong to the launch currently in flight.
    mutating func completeLaunch(token: UInt64, pid: Int32) -> Bool {
        guard case .launching(let expected) = phase, expected == token else { return false }
        phase = pid > 0 ? .running(token: token, pid: pid) : .stopped
        return true
    }

    /// Marks the in-flight launch as failed. Same staleness rule as
    /// `completeLaunch`.
    mutating func failLaunch(token: UInt64) -> Bool {
        guard case .launching(let expected) = phase, expected == token else { return false }
        phase = .stopped
        return true
    }

    /// Claims the active token for cancellation. Returns nil when nothing is
    /// launching or running, so a second tap cannot re-terminate a dead shell.
    mutating func requestStop() -> UInt64? {
        switch phase {
        case .launching(let token), .running(let token, _):
            phase = .stopped
            return token
        case .idle, .stopped:
            return nil
        }
    }

    /// True while this token's launch has not reported a PID yet, i.e. a
    /// launch watchdog should still fire.
    func isAwaitingPID(token: UInt64) -> Bool {
        if case .launching(let expected) = phase {
            return expected == token
        }
        return false
    }
}
