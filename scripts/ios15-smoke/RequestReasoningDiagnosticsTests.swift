import Foundation

@main
struct RequestReasoningDiagnosticsTests {
    static func main() throws {
        let cases: [([String: Any], String)] = [
            (["reasoning": ["effort": "xhigh", "summary": "auto"]], "reasoning.effort=xhigh reasoning_effort=omitted"),
            (["reasoning_effort": "high"], "reasoning.effort=omitted reasoning_effort=high"),
            ([:], "reasoning.effort=omitted reasoning_effort=omitted"),
            (["reasoning": ["summary": "auto"]], "reasoning.effort=omitted reasoning_effort=omitted"),
            (["reasoning": ["effort": NSNull()]], "reasoning.effort=null reasoning_effort=omitted"),
            (["reasoning_effort": 42], "reasoning.effort=omitted reasoning_effort=invalid-type"),
            (["reasoning_effort": "secret-value-must-not-be-echoed"], "reasoning.effort=omitted reasoning_effort=nonstandard"),
        ]
        for (body, expected) in cases {
            let before = try JSONSerialization.data(withJSONObject: body, options: .sortedKeys)
            precondition(RequestReasoningDiagnostics.summary(body: body) == expected)
            let after = try JSONSerialization.data(withJSONObject: body, options: .sortedKeys)
            precondition(after == before)
        }
        let body: [String: Any] = ["instructions": String(repeating: "private content", count: 1000),
                                   "reasoning": ["effort": "xhigh"]]
        let encoded = try JSONSerialization.data(withJSONObject: body, options: [.prettyPrinted, .sortedKeys])
        let oldPreview = String(String(decoding: encoded, as: UTF8.self).prefix(3000))
        precondition(!oldPreview.contains("xhigh"), "fixture must reproduce preview truncation")
        let summary = RequestReasoningDiagnostics.summary(body: body)
        precondition(summary.contains("reasoning.effort=xhigh") && !summary.contains("private content"))
        print("PASS: request reasoning metadata, truncation and non-mutation/privacy tests")
    }
}
