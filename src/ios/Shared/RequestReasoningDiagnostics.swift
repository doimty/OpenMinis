import Foundation

/// A bounded final-body summary, independent of the long prompt/body preview.
/// Observability only: never infer effort from returned reasoning-token counts,
/// and never change the body or log arbitrary values as if they were tiers.
enum RequestReasoningDiagnostics {
    private static let tiers: Set<String> = ["none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra", "auto"]

    static func summary(body: [String: Any]) -> String {
        let nested = (body["reasoning"] as? [String: Any])?["effort"]
        return "reasoning.effort=\(tier(nested)) reasoning_effort=\(tier(body["reasoning_effort"]))"
    }

    private static func tier(_ value: Any?) -> String {
        guard let value else { return "omitted" }
        if value is NSNull { return "null" }
        guard let string = value as? String else { return "invalid-type" }
        return tiers.contains(string) ? string : "nonstandard"
    }
}
