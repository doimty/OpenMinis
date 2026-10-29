import Foundation

private func canonicalJSON(_ object: [String: Any]) -> Data? {
    try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
}

private func check(
    _ name: String,
    _ result: ResolvedToolArguments,
    state expectedState: ToolArgumentsState,
    args expectedArgs: [String: Any]? = nil,
    raw expectedRaw: String? = nil
) {
    guard result.state == expectedState else {
        fatalError("\(name): expected state \(expectedState), got \(result.state)")
    }
    if let expectedArgs {
        guard canonicalJSON(result.args) == canonicalJSON(expectedArgs) else {
            fatalError("\(name): resolved arguments differ")
        }
    }
    if let expectedRaw, result.rawJSON != expectedRaw {
        fatalError("\(name): malformed raw payload was not retained for recovery")
    }
    print("PASS \(name)")
}

check(
    "arguments.done snapshot",
    ResponsesToolArgumentsResolver.resolve(delta: "", argumentsDone: #"{"command":"pwd"}"#, itemArguments: nil),
    state: .valid,
    args: ["command": "pwd"]
)
check(
    "output_item.done snapshot",
    ResponsesToolArgumentsResolver.resolve(delta: "", argumentsDone: nil, itemArguments: #"{"path":"/tmp/a"}"#),
    state: .valid,
    args: ["path": "/tmp/a"]
)
check(
    "delta-only payload",
    ResponsesToolArgumentsResolver.resolve(delta: #"{"command":"echo ok"}"#, argumentsDone: nil, itemArguments: nil),
    state: .valid,
    args: ["command": "echo ok"]
)
check(
    "explicit empty object remains valid",
    ResponsesToolArgumentsResolver.resolve(delta: "", argumentsDone: "{}", itemArguments: nil),
    state: .valid,
    args: [:]
)
check(
    "absent payload is missing",
    ResponsesToolArgumentsResolver.resolve(delta: "", argumentsDone: nil, itemArguments: nil),
    state: .missing,
    args: [:]
)
check(
    "malformed payload is distinguished",
    ResponsesToolArgumentsResolver.resolve(delta: "", argumentsDone: #"{"command":"# , itemArguments: nil),
    state: .malformedJSON,
    args: [:],
    raw: #"{"command":"#
)
check(
    "non-object JSON is distinguished",
    ResponsesToolArgumentsResolver.resolve(delta: "", argumentsDone: "[1,2]", itemArguments: nil),
    state: .nonObjectJSON,
    args: [:]
)
check(
    "disagreeing terminal snapshots fail closed",
    ResponsesToolArgumentsResolver.resolve(
        delta: "", argumentsDone: #"{"command":"done"}"#,
        itemArguments: #"{"command":"item"}"#
    ),
    state: .conflictingFinalValues,
    args: [:]
)
check(
    "stale complete delta conflicts with terminal snapshot",
    ResponsesToolArgumentsResolver.resolve(
        delta: #"{"command":"old"}"#, argumentsDone: #"{"command":"done"}"#, itemArguments: nil
    ),
    state: .conflictingFinalValues,
    args: [:]
)
check(
    "malformed terminal snapshot cannot fall back to delta",
    ResponsesToolArgumentsResolver.resolve(
        delta: #"{"command":"streamed"}"#, argumentsDone: #"{"command":"# , itemArguments: nil
    ),
    state: .conflictingFinalValues,
    args: [:]
)
check(
    "equivalent terminal snapshots ignore key order",
    ResponsesToolArgumentsResolver.resolve(
        delta: "", argumentsDone: #"{"a":1,"b":2}"#,
        itemArguments: #"{"b":2,"a":1}"#
    ),
    state: .valid,
    args: ["a": 1, "b": 2]
)
