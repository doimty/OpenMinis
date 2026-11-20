// Compile through: python3 tests/test_gpt62_reasoning.py --native
// Foundation-only smoke: the runner extracts production declarations verbatim.
// No app host, network, credentials, defaults, or device access. Python static
// checks are NOT Swift execution. Missing swiftc is SKIP / exit 77, never PASS.
import Foundation

@main
struct GPT62ReasoningTests {
    static func main() throws {
        var failures = 0
        func check(_ name: String, _ condition: Bool) {
            print("\(condition ? "PASS" : "FAIL")[\(name)]")
            if !condition { failures += 1 }
        }
        func model(_ id: String = "gpt-6.2-sol", support: Bool? = nil,
                   tiers: [String]? = nil, noTiers: Bool? = nil) -> LLMModel {
            LLMModel(id: id, displayName: id, provider: "relay",
                     supportsReasoning: support, reasoningEffortValues: tiers,
                     declaresNoEffortTiers: noTiers)
        }
        func responses(_ m: LLMModel, _ level: ThinkingLevel, codex: Bool = false,
                       off: String? = nil, mistral: Bool = false) -> [String: Any] {
            var body: [String: Any] = [:]
            OpenAIAgentProvider.injectResponsesThinkingParams(
                into: &body, model: m, level: level, isCodexOAuth: codex,
                isMistral: mistral, offEffort: off)
            return body
        }
        func chat(_ m: LLMModel, _ level: ThinkingLevel, router: Bool = false,
                  rules: [ThinkingRule] = [], off: String? = nil) -> [String: Any] {
            var body: [String: Any] = [:]
            let ctx = ThinkingResolveContext(
                modelId: m.id, supportsReasoning: m.effectiveSupportsReasoning,
                declaredEffortValues: level.isEnabled ? m.effectiveReasoningEffortValues : m.reasoningEffortValues,
                declaresNoEffortTiers: m.declaresNoEffortTiers ?? false,
                effortDeclarationIsAuthoritative: m.effortDeclarationIsAuthoritative ?? false,
                level: level, maxTokens: 4096, isOpenRouter: router,
                usesUnifiedReasoningEffort: false, isMistral: false,
                offEffort: off, userRules: rules)
            _ = ThinkingRuleResolver.apply(to: &body, ctx: ctx)
            return body
        }
        func nestedEffort(_ body: [String: Any]) -> String? {
            (body["reasoning"] as? [String: String])?["effort"]
        }

        let ids = ["gpt-6.2", "gpt-6-2", "GPT-6.2-ASTRA", "gpt-6-2-sol-2026-11-20",
                   "openai/gpt-6.2-sol", "vendor:openai/GPT-6-2-ASTRA-2026-11-20"]
        for id in ids {
            check("family.\(id)", ThinkingLevelCatalog.isGPT62Family(id))
            check("capability.\(id)", model(id).effectiveSupportsReasoning == true)
            check("ceiling.\(id)", model(id).catalogMaxThinkingLevel == .max)
        }
        for id in ["gpt-6.20-sol", "gpt-6-20-astra", "not-gpt-6.2-sol", "gpt-6.2sol",
                   "gpt-6.2.1-sol", "relay/gpt-4o", "gpt-6.2-sol/other", "gpt-6.2-sol/"] {
            check("negative.\(id)", !ThinkingLevelCatalog.isGPT62Family(id))
            check("noFallback.\(id)", model(id).effectiveSupportsReasoning == nil)
        }
        for (m, context) in [(LLMModel.gpt62Astra, 272_000), (LLMModel.gpt62Sol, 500_000)] {
            check("static.context.\(m.id)", m.contextWindow == context && m.maxOutputTokens == 128_000)
            check("static.oauth.\(m.id)", LLMModel.allOpenAICodexOAuth.contains { $0.id == m.id })
            check("static.noApiKey.\(m.id)", !LLMModel.allOpenAI.contains { $0.id == m.id })
            check("static.tiers.\(m.id)", m.reasoningEffortValues == ["low", "medium", "high", "xhigh", "max"])
        }
        check("legacy.oauthOrder", LLMModel.allOpenAICodexOAuth.first?.id == "gpt-6-astra")
        let legacy = try JSONDecoder().decode(LLMModel.self, from: Data(
            #"{"id":"relay/GPT-6-2-SOL-2026-11-20","displayName":"old","provider":"relay"}"#.utf8))
        check("decode.rawNil", legacy.supportsReasoning == nil && legacy.reasoningEffortValues == nil)
        check("decode.effective", legacy.effectiveSupportsReasoning == true && legacy.catalogMaxThinkingLevel == .max)
        let encoded = try JSONSerialization.jsonObject(with: JSONEncoder().encode(legacy)) as? [String: Any]
        check("decode.notPersisted", encoded?["supportsReasoning"] == nil && encoded?["reasoningEffortValues"] == nil)

        for (level, effort) in [(ThinkingLevel.low, "low"), (.medium, "medium"), (.high, "high"),
                                (.xhigh, "xhigh"), (.max, "max"), (.ultra, "max")] {
            check("effort.\(level.rawValue)", OpenAIAgentProvider.reasoningEffort(for: legacy, level: level) == effort)
            let c = chat(legacy, level)
            check("chat.\(level.rawValue)", c["reasoning_effort"] as? String == effort && c["reasoning"] == nil)
            let r = responses(legacy, level, codex: true)
            check("responses.\(level.rawValue)", nestedEffort(r) == effort && r["reasoning_effort"] == nil)
            check("responses.include.\(level.rawValue)", r["include"] as? [String] == ["reasoning.encrypted_content"])
        }
        for blocked in [model(support: false), model(noTiers: true), model(tiers: []), model(tiers: ["unknown", "ultra"])] {
            check("blocked.ceiling", blocked.catalogMaxThinkingLevel == .off)
            check("blocked.effort", OpenAIAgentProvider.reasoningEffort(for: blocked, level: .max) == nil)
            for level in [ThinkingLevel.off, .max] {
                check("blocked.chat", chat(blocked, level, off: "none").isEmpty)
                check("blocked.router", chat(blocked, level, router: true).isEmpty)
                check("blocked.responses", responses(blocked, level, codex: true, off: "none").isEmpty)
            }
        }
        let narrow = model(tiers: ["LOW", "medium", "high"])
        check("narrow.ceiling", narrow.catalogMaxThinkingLevel == .high)
        check("narrow.levels", narrow.selectableThinkingLevels == [.low, .medium, .high])
        check("narrow.chat", chat(narrow, .ultra)["reasoning_effort"] as? String == "high")
        check("narrow.router", nestedEffort(chat(narrow, .ultra, router: true)) == "high")
        check("narrow.responses", nestedEffort(responses(narrow, .ultra)) == "high")
        let sparse = model(tiers: ["high", "max"])
        check("sparse.down", nestedEffort(responses(sparse, .xhigh)) == "high")
        check("sparse.up.enabled", nestedEffort(responses(sparse, .low)) == "high")
        check("sparse.off.notRaised", responses(sparse, .off, codex: true).isEmpty)
        check("off.nil", OpenAIAgentProvider.reasoningEffort(for: legacy, level: .off) == nil)
        check("off.relay.chat", chat(legacy, .off).isEmpty)
        check("off.relay.responses", responses(legacy, .off).isEmpty)
        check("off.official.chat", chat(legacy, .off, off: "none")["reasoning_effort"] as? String == "none")
        check("off.official.responses", nestedEffort(responses(legacy, .off, off: "none")) == "none")
        check("off.codex.low", nestedEffort(responses(legacy, .off, codex: true)) == "low")
        check("mistral", responses(legacy, .max, codex: true, mistral: true).isEmpty)
        let unrelated = model("gpt-6-astra")
        check("legacy.nilNotEnabled", OpenAIAgentProvider.reasoningEffort(for: unrelated, level: .max) == nil)
        let custom = ThinkingRule(kind: .custom, scope: .allModels,
                                  wireFormat: .customPath(path: "custom_tier", values: [.max: "chosen"], offValue: nil),
                                  label: "user-first")
        check("custom.priority", chat(legacy, .max, rules: [custom])["custom_tier"] as? String == "chosen")
        check("custom.notReplaced", chat(legacy, .max, rules: [custom])["reasoning_effort"] == nil)

        var snapshot = narrow
        snapshot.effortDeclarationIsAuthoritative = true
        snapshot.voiceRole = "none"
        snapshot.limitsReportedByEndpoint = true
        let entry = ModelEntry(providerInstanceId: "fixture", model: snapshot,
                               overrides: ModelOverrides(displayName: "renamed"))
        check("rebuild.tiers", entry.model.reasoningEffortValues == snapshot.reasoningEffortValues)
        check("rebuild.authority", entry.model.effortDeclarationIsAuthoritative == true)
        check("rebuild.otherMetadata", entry.model.voiceRole == "none" && entry.model.limitsReportedByEndpoint == true)
        check("rebuild.clamp", nestedEffort(responses(entry.model, .max)) == "high")
        let disabledEntry = ModelEntry(providerInstanceId: "fixture", model: snapshot,
                                       overrides: ModelOverrides(supportsReasoning: false))
        check("override.false", responses(disabledEntry.model, .max, codex: true).isEmpty)
        let noTierEntry = ModelEntry(providerInstanceId: "fixture", model: model(noTiers: true),
                                    overrides: ModelOverrides(displayName: "renamed"))
        check("rebuild.noTiers", noTierEntry.model.declaresNoEffortTiers == true)
        check("rebuild.baseUnchanged", entry.baseModel.displayName == snapshot.displayName)

        let endpoint = Data(#"{"models":[{"slug":"gpt-6.2-sol","supports_reasoning":false,"supported_reasoning_levels":[{"effort":"max"}]},{"id":"gpt-6.2-astra","supported_reasoning_levels":[{"effort":"low"},{"effort":"high"}]}]}"#.utf8)
        let fresh = OpenAIModelsAPI.parseCodexModels(endpoint)
        check("endpoint.falseWins", fresh[0].supportsReasoning == false)
        check("endpoint.tiers", fresh[1].reasoningEffortValues == ["low", "high"])
        check("endpoint.authority", fresh[1].effortDeclarationIsAuthoritative == true)
        let stale = [model("gpt-6.2-sol", support: true), model("gpt-6.2-astra", support: true)]
        let merged = OpenAIModelsAPI.smokeMerge(fresh: fresh, enriched: stale)
        check("refresh.false", merged[0].supportsReasoning == false)
        check("refresh.tiers", merged[1].reasoningEffortValues == ["low", "high"])
        check("refresh.authority", merged[1].effortDeclarationIsAuthoritative == true)
        check("refresh.wire", nestedEffort(responses(merged[1], .max)) == "high")
        var relay = model("vendor/GPT-6-2-SOL")
        OpenAIModelsAPI.applyGPT62ReasoningMetadata(
            ["supportsReasoning": true, "reasoningEffortValues": ["medium", "high"], "declaresNoEffortTiers": false],
            to: &relay)
        check("relay.metadata", relay.reasoningEffortValues == ["medium", "high"] && relay.effortDeclarationIsAuthoritative == true)
        var empty = model()
        OpenAIModelsAPI.applyGPT62ReasoningMetadata(["supported_reasoning_levels": []], to: &empty)
        check("endpoint.empty", empty.declaresNoEffortTiers == true && responses(empty, .max, codex: true).isEmpty)
        var absent = model()
        OpenAIModelsAPI.applyGPT62ReasoningMetadata([:], to: &absent)
        check("endpoint.absentNotPersisted", absent.supportsReasoning == nil && absent.reasoningEffortValues == nil)
        print("SUMMARY fail=\(failures)")
        if failures != 0 { exit(1) }
    }
}
