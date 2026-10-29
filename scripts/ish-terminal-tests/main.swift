import Foundation

// Standalone native macOS regression harness for the interactive terminal
// shell lifecycle. No app, kernel, or main run loop.
private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError("FAIL: \(message)") }
}

private func testSingleFlight() {
    var life = ISHTerminalShellLifecycle()
    expect(life.beginLaunch(token: 7), "first launch claims the slot")
    expect(life.phase == .launching(token: 7), "phase is launching")
    expect(!life.beginLaunch(token: 8), "second launch is rejected while launching")
    expect(life.phase == .launching(token: 7), "rejected launch did not replace the token")
    expect(life.isActive, "terminal is active while launching")
    expect(life.isAwaitingPID(token: 7), "watchdog window is open")
    expect(!life.isAwaitingPID(token: 8), "watchdog ignores foreign tokens")
}

private func testZeroTokenRejected() {
    var life = ISHTerminalShellLifecycle()
    expect(!life.beginLaunch(token: 0), "token zero is not an identity")
    expect(life.phase == .idle, "phase stays idle")
    expect(!life.isActive, "idle terminal is not active")
}

private func testLaunchReportMustMatchToken() {
    var life = ISHTerminalShellLifecycle()
    _ = life.beginLaunch(token: 11)
    expect(!life.completeLaunch(token: 12, pid: 99), "foreign report is ignored")
    expect(life.phase == .launching(token: 11), "phase unchanged after foreign report")
    expect(life.completeLaunch(token: 11, pid: 99), "matching report is applied")
    expect(life.phase == .running(token: 11, pid: 99), "phase is running")
    expect(!life.isAwaitingPID(token: 11), "watchdog window closes once running")
    expect(life.isActive, "running terminal is active")
    expect(!life.completeLaunch(token: 11, pid: 100), "second report for the same launch is ignored")
    expect(life.phase == .running(token: 11, pid: 99), "duplicate report cannot rewrite the PID")
}

private func testLaunchFailure() {
    var life = ISHTerminalShellLifecycle()
    _ = life.beginLaunch(token: 21)
    expect(!life.failLaunch(token: 22), "failure for a foreign token is ignored")
    expect(life.failLaunch(token: 21), "matching failure is applied")
    expect(life.phase == .stopped, "failed launch ends stopped")
    expect(!life.isActive, "failed terminal is not active")
    expect(!life.failLaunch(token: 21), "failure cannot be applied twice")
    expect(!life.completeLaunch(token: 21, pid: 5), "late success after failure is ignored")
}

private func testNonPositivePIDEndsStopped() {
    var life = ISHTerminalShellLifecycle()
    _ = life.beginLaunch(token: 31)
    expect(life.completeLaunch(token: 31, pid: 0), "zero PID report is accepted as the answer")
    expect(life.phase == .stopped, "no guest process means stopped")
    expect(!life.isActive, "terminal is not active without a guest process")
}

private func testStopIsIdempotent() {
    var life = ISHTerminalShellLifecycle()
    _ = life.beginLaunch(token: 41)
    _ = life.completeLaunch(token: 41, pid: 4242)
    expect(life.requestStop() == 41, "stop claims the live token")
    expect(life.phase == .stopped, "stop ends the lifecycle")
    expect(life.requestStop() == nil, "second stop has nothing to claim")
    expect(!life.completeLaunch(token: 41, pid: 4243), "report after stop is ignored")
    expect(!life.failLaunch(token: 41), "failure after stop is ignored")
}

private func testStopBeforeLaunchReport() {
    var life = ISHTerminalShellLifecycle()
    _ = life.beginLaunch(token: 51)
    expect(life.requestStop() == 51, "stop can abandon a launch still waiting for a PID")
    expect(life.phase == .stopped, "abandoned launch ends stopped")
    expect(!life.isAwaitingPID(token: 51), "watchdog must not fire after stop")
    expect(!life.completeLaunch(token: 51, pid: 77), "kernel report after stop cannot revive the shell")
}

private func testRestartAfterStopUsesNewToken() {
    var life = ISHTerminalShellLifecycle()
    _ = life.beginLaunch(token: 61)
    _ = life.completeLaunch(token: 61, pid: 600)
    _ = life.requestStop()
    expect(life.beginLaunch(token: 62), "a stopped terminal can launch again")
    expect(life.phase == .launching(token: 62), "restart uses the new token")
    expect(!life.completeLaunch(token: 61, pid: 601), "old token report cannot touch the new launch")
    expect(life.completeLaunch(token: 62, pid: 602), "new token report is applied")
    expect(life.phase == .running(token: 62, pid: 602), "new shell is running")
}

private func testIdleStopAndReportAreNoOps() {
    var life = ISHTerminalShellLifecycle()
    expect(life.requestStop() == nil, "idle terminal has nothing to stop")
    expect(!life.completeLaunch(token: 71, pid: 1), "idle terminal ignores launch reports")
    expect(!life.failLaunch(token: 71), "idle terminal ignores failures")
    expect(life.phase == .idle, "idle stays idle")
}

let tests: [(String, () -> Void)] = [
    ("single flight slot", testSingleFlight),
    ("zero token is not an identity", testZeroTokenRejected),
    ("launch report must match its token", testLaunchReportMustMatchToken),
    ("launch failure ends stopped", testLaunchFailure),
    ("non-positive PID ends stopped", testNonPositivePIDEndsStopped),
    ("stop is idempotent", testStopIsIdempotent),
    ("stop before launch report", testStopBeforeLaunchReport),
    ("restart uses a fresh token", testRestartAfterStopUsesNewToken),
    ("idle stop and reports are no-ops", testIdleStopAndReportAreNoOps)
]

for (name, test) in tests {
    test()
    print("PASS: \(name)")
}
print("PASS: \(tests.count) native ISHTerminalShellLifecycle regression groups")
