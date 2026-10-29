import Foundation

/// Validity attached to a provider-parsed tool argument object. `nil` on
/// `ToolCallMetadata` means the provider does not supply this validation
/// signal; Responses API supplies it for every function call.
enum ToolArgumentsState: String, Sendable, Equatable {
    case valid
    case missing
    case malformedJSON
    case nonObjectJSON
    case conflictingFinalValues
}

/// Result of resolving Responses API argument snapshots. Kept Foundation-only
/// so the exact production resolver can be compiled and tested without the
/// app's device-only native frameworks or a live network stream.
struct ResolvedToolArguments: @unchecked Sendable {
    let args: [String: Any]
    let state: ToolArgumentsState
    /// Only retained for repair of a truncated object; never log this payload.
    let rawJSON: String?
}

enum ResponsesToolArgumentsResolver {
    private struct Candidate {
        let state: ToolArgumentsState
        let args: [String: Any]?
        let raw: String
    }

    private static func parse(_ raw: String?) -> Candidate? {
        guard let raw, !raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return nil }
        guard let data = raw.data(using: .utf8),
              let value = try? JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed]) else {
            return Candidate(state: .malformedJSON, args: nil, raw: raw)
        }
        guard let object = value as? [String: Any] else {
            return Candidate(state: .nonObjectJSON, args: nil, raw: raw)
        }
        return Candidate(state: .valid, args: object, raw: raw)
    }

    private static func canonical(_ object: [String: Any]) -> Data? {
        try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    }

    /// `arguments.done` and `output_item.done.item.arguments` are complete
    /// snapshots. Prefer either valid terminal snapshot over the streamed
    /// delta, but reject disagreement between complete snapshots (or between
    /// a complete snapshot and a complete streamed object) rather than
    /// guessing which tool input the endpoint intended. An explicit terminal
    /// `{}` is therefore valid; it is never silently replaced by stale delta.
    static func resolve(delta: String, argumentsDone: String?, itemArguments: String?) -> ResolvedToolArguments {
        let done = parse(argumentsDone)
        let item = parse(itemArguments)
        let streamed = parse(delta)
        let terminalSnapshots = [done, item].compactMap { $0 }
        let terminal = terminalSnapshots.filter { $0.state == .valid }

        if !terminal.isEmpty, terminalSnapshots.contains(where: { $0.state != .valid }) {
            return ResolvedToolArguments(args: [:], state: .conflictingFinalValues, rawJSON: nil)
        }
        if terminal.count > 1,
           canonical(terminal[0].args ?? [:]) != canonical(terminal[1].args ?? [:]) {
            return ResolvedToolArguments(args: [:], state: .conflictingFinalValues, rawJSON: nil)
        }
        if let final = terminal.first {
            if let streamed, streamed.state == .valid,
               canonical(final.args ?? [:]) != canonical(streamed.args ?? [:]) {
                return ResolvedToolArguments(args: [:], state: .conflictingFinalValues, rawJSON: nil)
            }
            return ResolvedToolArguments(args: final.args ?? [:], state: .valid, rawJSON: nil)
        }
        if let streamed, streamed.state == .valid {
            if !terminalSnapshots.isEmpty {
                return ResolvedToolArguments(args: [:], state: .conflictingFinalValues, rawJSON: nil)
            }
            return ResolvedToolArguments(args: streamed.args ?? [:], state: .valid, rawJSON: nil)
        }

        let invalid = [done, item, streamed].compactMap { $0 }.first
        guard let invalid else {
            return ResolvedToolArguments(args: [:], state: .missing, rawJSON: nil)
        }
        return ResolvedToolArguments(args: [:], state: invalid.state, rawJSON: invalid.raw)
    }
}
