import Dispatch
import Foundation

@main
enum DeviceMCPApprovalTests {
    static func main() {
        func request(_ operation: String = "get_brightness", _ percent: String? = nil) -> DeviceMCPRequest? {
            DeviceMCPRequest(id: UUID().uuidString, operation: operation, value: percent)
        }
        for name in ["get_device_info", "get_screen_info", "get_brightness"] {
            precondition(request(name) != nil && request(name, "20") == nil)
        }
        for value in [nil, "", "01", "+10", "-1", "00", "0.5", "101", " 2", "１２"] {
            precondition(request("set_brightness", value) == nil)
        }
        for value in ["0", "1", "50", "100"] { precondition(request("set_brightness", value) != nil) }
        precondition(request("status") == nil && request("read_file") == nil)
        precondition(DeviceMCPRequest(id: "9", operation: "get_brightness", value: nil) == nil)

        var gate = DeviceMCPGate()
        let first = request("set_brightness", "30")!, second = request()!
        precondition(gate.begin(first, foreground: true) == "unavailable")
        gate.setEnabled(true)
        precondition(gate.begin(first, foreground: false) == "unavailable")
        precondition(gate.begin(first, foreground: true) == nil)
        precondition(gate.begin(second, foreground: true) == "busy")
        precondition(!gate.end(second, allow: true, foreground: true))
        precondition(!gate.end(first, allow: false, foreground: true))
        precondition(gate.begin(first, foreground: true) == "duplicate")
        precondition(gate.begin(second, foreground: true) == nil)
        gate.setEnabled(false)
        gate.setEnabled(true)
        precondition(!gate.end(second, allow: true, foreground: true))
        let backgrounded = request()!
        precondition(gate.begin(backgrounded, foreground: true) == nil)
        precondition(!gate.end(backgrounded, allow: true, foreground: false))
        let once = request()!
        precondition(gate.begin(once, foreground: true) == nil)
        precondition(gate.end(once, allow: true, foreground: true))
        precondition(!gate.end(once, allow: true, foreground: true))

        var tick = 0.0
        var writes = 0
        let stale = DeviceMCPTransaction(request: first, timeout: 0.5, now: { tick })
        tick = 0.5
        precondition(!stale.canPresent)
        _ = stale.finish { writes += 1; return ["ok": true] }
        precondition(stale.wait()["error"] as? String == "expired" && writes == 0)
        let arrivedLate = DeviceMCPTransaction(request: second, timeout: 0.5, now: { tick })
        tick += 1
        precondition(arrivedLate.wait()["error"] as? String == "expired")
        _ = arrivedLate.finish { writes += 1; return ["ok": true] }
        precondition(writes == 0)
        let cancelled = DeviceMCPTransaction(request: first)
        precondition(cancelled.wait(cancelled: { true })["error"] as? String == "revoked")
        _ = cancelled.finish { writes += 1; return ["ok": true] }
        precondition(writes == 0)
        let accepted = DeviceMCPTransaction(request: once)
        let concurrency = DispatchQueue(label: "test.device-mcp.concurrent", attributes: .concurrent)
        let callbacks = DispatchGroup()
        let counter = Counter()
        for _ in 0..<32 {
            concurrency.async(group: callbacks) {
                _ = accepted.finish { counter.increment(); return ["ok": true] }
            }
        }
        precondition(callbacks.wait(timeout: .now() + 3) == .success)
        precondition(counter.count == 1 && accepted.wait()["ok"] as? Bool == true)
        print("Device MCP validation, denial, revocation, replay, expiry and atomic consume: PASS")
    }

    private final class Counter: @unchecked Sendable {
        private let lock = NSLock()
        private var value = 0
        func increment() { lock.lock(); value += 1; lock.unlock() }
        var count: Int { lock.lock(); defer { lock.unlock() }; return value }
    }
}
