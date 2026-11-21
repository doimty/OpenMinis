#if MINIS_ROOT_MANAGEMENT
import Foundation

enum DeviceMCPOperation: String, Sendable {
    case device = "get_device_info", screen = "get_screen_info", brightness = "get_brightness", setBrightness = "set_brightness"
}

struct DeviceMCPRequest: Equatable, Sendable {
    let id: UUID
    let operation: DeviceMCPOperation
    let percent: Int?

    init?(id: String, operation: String, value: String?) {
        guard id.utf8.count == 36, let uuid = UUID(uuidString: id), uuid.uuidString.lowercased() == id.lowercased(),
              let action = DeviceMCPOperation(rawValue: operation) else { return nil }
        if action == .setBrightness {
            guard let value, (1...3).contains(value.utf8.count), value.utf8.allSatisfy({ (48...57).contains($0) }),
                  let number = Int(value), (0...100).contains(number), String(number) == value else { return nil }
            percent = number
        } else {
            guard value == nil else { return nil }
            percent = nil
        }
        self.id = uuid
        self.operation = action
    }
}

struct DeviceMCPGate {
    private(set) var enabled = false
    private(set) var pending: DeviceMCPRequest?
    private var seen: Set<UUID> = []

    mutating func setEnabled(_ value: Bool) {
        enabled = value
        if !value {
            pending = nil
            seen.removeAll(keepingCapacity: true)
        }
    }

    mutating func begin(_ request: DeviceMCPRequest, foreground: Bool) -> String? {
        guard !seen.contains(request.id) else { return "duplicate" }
        guard enabled, foreground else { return "unavailable" }
        guard pending == nil else { return "busy" }
        guard seen.count < 2048 else { return "limit" }
        seen.insert(request.id)
        pending = request
        return nil
    }

    mutating func end(_ request: DeviceMCPRequest, allow: Bool, foreground: Bool) -> Bool {
        guard pending == request else { return false }
        pending = nil
        return allow && enabled && foreground
    }
}

final class DeviceMCPTransaction: @unchecked Sendable {
    typealias Result = [String: Any]
    let request: DeviceMCPRequest
    private let deadline: TimeInterval
    private let now: () -> TimeInterval
    private let lock = NSCondition()
    private var finished: Result?

    init(request: DeviceMCPRequest, timeout: TimeInterval = 30,
         now: @escaping () -> TimeInterval = { ProcessInfo.processInfo.systemUptime }) {
        self.request = request
        self.now = now
        self.deadline = now() + timeout
    }

    func response(error: String) -> Result {
        ["ok": false, "request_id": request.id.uuidString.lowercased(), "operation": request.operation.rawValue, "error": error]
    }

    func finish(_ produce: () -> Result) -> Result {
        lock.lock()
        defer { lock.unlock() }
        if let finished { return finished }
        let result = now() < deadline ? produce() : response(error: "expired")
        finished = result
        lock.broadcast()
        return result
    }

    func wait(cancelled: () -> Bool = { false }) -> Result {
        lock.lock()
        defer { lock.unlock() }
        while finished == nil {
            if cancelled() {
                finished = response(error: "revoked")
                break
            }
            let remaining = deadline - now()
            guard remaining > 0 else {
                finished = response(error: "expired")
                break
            }
            _ = lock.wait(until: Date(timeIntervalSinceNow: min(remaining, 0.25)))
        }
        return finished!
    }

    var canPresent: Bool {
        lock.lock()
        defer { lock.unlock() }
        return finished == nil && now() < deadline
    }
}
#endif
