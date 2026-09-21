import Foundation

// Only downstream services are doubled. The production bridge's class name,
// dynamic C lookup, selectors, singleton, locks and dispatch body are unchanged.
@MainActor
final class DebugViewInspector {}

final class DebugJSONRPC {
    @MainActor init(inspector: DebugViewInspector) {}
    func handle(json: String) async -> String {
        let result: [String: Any] = ["jsonrpc": "2.0", "id": 1,
            "result": ["fixtureRPC": true, "unchangedEnvelope": json]]
        let data = try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        return String(decoding: data, as: UTF8.self)
    }
}

// The log reader is compiled for its runtime-name/selector contract only.
// The test deliberately never invokes its OSLogStore or filesystem methods.
final class LoggingManager {
    static let shared = LoggingManager()
    var isEnabled: Bool { false }
    struct Entry { let url: URL }
    func logFiles() -> [Entry] { [] }
    func readLog(at url: URL, maxBytes: Int) -> String { "" }
}
