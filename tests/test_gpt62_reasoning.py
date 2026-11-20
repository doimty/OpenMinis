#!/usr/bin/env python3
"""GPT62 Python reference matrix + source integration; NOT Swift/native proof.

--static runs these checks. --native compiles verbatim production slices with
Foundation-only smoke tests, or exits 77 (SKIP) if swiftc is absent. No network,
app host, credentials, defaults, or device access. --emit-swift is extraction
only and must never be reported as a compilation/run pass.
"""
import argparse
import json
import platform
import sys
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "src/ios/Providers"
TIERS = ["low", "medium", "high", "xhigh", "max"]
LADDER = ["none", "minimal"] + TIERS


def read(rel):
    return (P / rel).read_text()


def mask_swift(text):
    pattern = r'//[^\n]*|/\*[\s\S]*?\*/|"""[\s\S]*?"""|"(?:\\.|[^"\\])*"'
    return re.sub(pattern, lambda m: re.sub(r"[^\n]", " ", m[0]), text)


def declaration(text, marker):
    start = text.index(marker)
    masked = mask_swift(text)
    opening = masked.index("{", start)
    depth = 1
    for i in range(opening + 1, len(text)):
        depth += (masked[i] == "{") - (masked[i] == "}")
        if depth == 0:
            return text[start:i + 1]
    raise ValueError(f"unclosed declaration: {marker}")


def family(model_id):
    # Independent, delimiter-bounded reference; source wiring is checked below.
    bare = re.split(r"[/:]", model_id.lower())[-1]
    return any(bare == stem or bare.startswith(stem + "-") for stem in ["gpt-6.2", "gpt-6-2"])


def effective_tiers(model_id, support=None, tiers=None, no_tiers=None):
    if not family(model_id):
        return tiers
    if support is False or no_tiers is True:
        return []
    if tiers is None:
        return TIERS
    return [tier.lower() for tier in tiers if tier.lower() in LADDER]


def clamp(effort, tiers):
    if not tiers or effort in tiers:
        return effort
    declared = sorted((LADDER.index(v), v) for v in tiers if v in LADDER)
    below = [v for index, v in declared if index <= LADDER.index(effort)]
    return below[-1] if below else declared[0][1]


def reference_effort(model_id, level, support=None, tiers=None, no_tiers=None):
    enabled = support if support is not None else family(model_id)
    if not enabled or level == "off":
        return None
    allowed = effective_tiers(model_id, support, tiers, no_tiers)
    if family(model_id) and not allowed:
        return None
    effort = "max" if level == "ultra" else level
    return clamp(effort, allowed) if family(model_id) else effort


class GPT62StaticChecks(unittest.TestCase):
    def test_static_models_and_oauth_only(self):
        src = read("LLMTypes.swift")
        for name, model_id, context in [("gpt62Astra", "gpt-6.2-astra", "272_000"),
                                        ("gpt62Sol", "gpt-6.2-sol", "500_000")]:
            match = re.search(rf"static let {name} = LLMModel\((.*?)\n    \)", src, re.S)
            self.assertIsNotNone(match, f"missing {name}")
            block = match[1]
            for required in [f'id: "{model_id}"', 'provider: "OpenAI"',
                             f"contextWindow: {context}", "maxOutputTokens: 128_000",
                             "supportsReasoning: true", "reasoningEffortValues: ThinkingLevelCatalog.gpt62EffortValues"]:
                self.assertIn(required, block)
            oauth = re.search(r"static let allOpenAICodexOAuth: \[LLMModel\] = \[(.*?)\n    \]", src, re.S)[1]
            api_key = re.search(r"static let allOpenAI: \[LLMModel\] = \[(.*?)\n    \]", src, re.S)[1]
            self.assertIn("." + name, oauth)
            self.assertNotIn("." + name, api_key)
        self.assertIn(".gpt6Astra, .gpt61Sol, .gpt6Sol, .gpt6Luna,", oauth)

    def test_family_source_and_boundaries(self):
        src = read("ThinkingLevelCatalog.swift")
        block = declaration(src, "static func isGPT62Family(")
        for required in ['lowercased()', 'CharacterSet(charactersIn: "/:")',
                         '"gpt-6.2"', '"gpt-6-2"', 'bare == $0', 'bare.hasPrefix($0 + "-")']:
            self.assertIn(required, block)
        self.assertIn('Self.isGPT62Family($0)', src)
        declared = re.search(r"gpt62EffortValues = (\[[^\]]*\])", src)[1]
        self.assertEqual(json.loads(declared), TIERS)
        positive = ["gpt-6.2", "gpt-6-2", "GPT-6.2-SOL", "gpt-6-2-astra-2026-11-20",
                    "openai/gpt-6.2-sol", "vendor:openai/GPT-6-2-ASTRA"]
        negative = ["gpt-6.20-sol", "gpt-6-20", "not-gpt-6.2-sol", "gpt-6.2sol",
                    "gpt-6.2.1-sol", "relay/gpt-4o", "gpt-6.2-sol/other", "gpt-6.2-sol/"]
        for model_id in positive + negative:
            with self.subTest(model_id=model_id):
                self.assertEqual(family(model_id), model_id in positive)

    def test_computed_policy_and_legacy_decode_wiring(self):
        src = read("LLMTypes.swift")
        support = declaration(src, "var effectiveSupportsReasoning:")
        tiers = declaration(src, "var effectiveReasoningEffortValues:")
        ceiling = declaration(src, "var catalogMaxThinkingLevel:")
        self.assertIn("supportsReasoning ??", support)
        self.assertIn("ThinkingLevelCatalog.isGPT62Family(id)", support)
        for required in ["supportsReasoning != false", "declaresNoEffortTiers != true", "return []",
                         "guard let declared = reasoningEffortValues else", "gpt62EffortValues"]:
            self.assertIn(required, tiers)
        self.assertIn("return selectableThinkingLevels.last ?? .off", ceiling)
        self.assertIn("effectiveReasoningEffortValues", declaration(src, "var selectableThinkingLevels:"))
        model = declaration(src, "struct LLMModel:")
        self.assertNotIn("init(from decoder:", model)
        self.assertNotIn("self.supportsReasoning = effective", model)
        self.assertNotIn("self.reasoningEffortValues = effective", model)

    def test_python_reference_matrix(self):
        # Expected literals are spec/registry oracles, not a Swift execution claim.
        for model_id in ["gpt-6.2-sol", "vendor/GPT-6-2-ASTRA-2026-11-20"]:
            for level in ["off"] + TIERS + ["ultra"]:
                with self.subTest(model_id=model_id, level=level):
                    expected = None if level == "off" else ("max" if level == "ultra" else level)
                    self.assertEqual(reference_effort(model_id, level), expected)
            for kwargs in [dict(support=False), dict(no_tiers=True), dict(tiers=[]), dict(tiers=["unknown", "ultra"])]:
                self.assertIsNone(reference_effort(model_id, "max", **kwargs))
            self.assertEqual(reference_effort(model_id, "ultra", tiers=["LOW", "medium", "high"]), "high")
            self.assertEqual(reference_effort(model_id, "xhigh", tiers=["high", "max"]), "high")
            self.assertEqual(reference_effort(model_id, "low", tiers=["high", "max"]), "high")
            self.assertIsNone(reference_effort(model_id, "off", tiers=["high", "max"]))
        for unrelated in ["gpt-6-astra", "gpt-6.20-sol", "local-llama"]:
            self.assertIsNone(reference_effort(unrelated, "max"))

    def test_agent_and_responses_wiring(self):
        src = read("OpenAI/OpenAIAgentProvider.swift")
        effort = declaration(src, "static func reasoningEffort(")
        self.assertIn("model.effectiveSupportsReasoning", effort)
        self.assertIn("clampEffort", effort)
        responses = declaration(src, "static func injectResponsesThinkingParams(")
        for required in ['body["reasoning"]', 'body["include"]', '"reasoning.encrypted_content"',
                         '''body["reasoning"] = ["effort": effort, "summary": "auto"]''',
                         '"effort": "low"', 'effectiveReasoningEffortValues', 'isMistral']:
            self.assertIn(required, responses)
        self.assertNotIn('body["reasoning_effort"]', responses)
        self.assertIn("Self.injectResponsesThinkingParams(", declaration(src, "private func streamResponsesAPI("))
        inject = declaration(src, "static func injectThinkingParams(")
        self.assertIn("supportsReasoning: model.effectiveSupportsReasoning", inject)
        self.assertIn("level.isEnabled ? model.effectiveReasoningEffortValues : model.reasoningEffortValues", inject)
        self.assertIn("ThinkingRuleResolver.apply(to: &body, ctx: ctx)", inject)
        resolver = read("Thinking/ThinkingRuleResolver.swift")
        self.assertIn("ThinkingLevelCatalog.isGPT62Family(ctx.modelId)", resolver)
        self.assertIn("ctx.declaresNoEffortTiers", resolver)
        self.assertIn("case .reasoningEffort, .reasoningEffortNested:", resolver)

    def test_refresh_metadata_and_rebuild_wiring(self):
        src = read("OpenAI/OpenAIModelsAPI.swift")
        parse = declaration(src, "static func parseCodexModels(")
        fetch = declaration(src, "private static func performFetch(")
        self.assertIn("applyGPT62ReasoningMetadata(item, to: &model)", parse)
        self.assertIn("applyGPT62ReasoningMetadata(item, to: &model)", fetch)
        metadata = declaration(src, "static func applyGPT62ReasoningMetadata(")
        for key in ["supports_reasoning", "supportsReasoning", "supported_reasoning_levels",
                    "reasoning_effort_values", "reasoningEffortValues", "declares_no_effort_tiers",
                    "declaresNoEffortTiers", "effortDeclarationIsAuthoritative"]:
            self.assertIn(key, metadata)
        merge = declaration(src, "private static func mergeKeepingAuthoritative(")
        for field in ["reasoningEffortValues", "declaresNoEffortTiers", "effortDeclarationIsAuthoritative"]:
            self.assertIn(f"merged.{field} = authoritative.{field}", merge)
        rebuilt = declaration(read("ModelEntry.swift"), "var model: LLMModel")
        for field in ["reasoningEffortValues", "declaresNoEffortTiers"]:
            self.assertIn(f"{field}: baseModel.{field}", rebuilt)
        self.assertIn("rebuilt.effortDeclarationIsAuthoritative = baseModel.effortDeclarationIsAuthoritative", rebuilt)
        dev = declaration(read("ModelsDevAPI.swift"), "private static func applyDevData(")
        self.assertIn("ThinkingLevelCatalog.isGPT62Family(model.id)", dev)
        for field in ["supportsReasoning", "reasoningEffortValues", "declaresNoEffortTiers"]:
            self.assertIn(f"model.{field} != nil", dev)
        self.assertIn("model.declaresNoEffortTiers == true", dev)
        self.assertIn("!(keepReasoningDeclarations &&", dev)


def extracted_swift():
    types = read("LLMTypes.swift")
    # Static model declarations plus the real stored fields/synthesized Codable.
    prefix = types.split("    // MARK: - Modality Inference from Model Name", 1)[0] + "\n}\n"
    pure_types = prefix + "\n" + declaration(types, "enum ThinkingLevel:")
    pure_types += "\n" + declaration(types, "extension LLMModel {")
    pure_types += "\n" + read("ThinkingLevelCatalog.swift") + "\n" + read("ModelEntry.swift")
    pure_types += "\n" + read("Thinking/ThinkingWireFormat.swift") + "\n" + read("Thinking/ThinkingRule.swift")
    pure_types += "\n" + declaration(read("Thinking/ThinkingRuleCoding.swift"), "extension ThinkingRule.Scope {")
    resolver = read("Thinking/ThinkingRuleResolver.swift")
    # Only the UI/store display method is outside the pure rule engine slice.
    pure_types += "\n" + resolver.rsplit("    @MainActor", 1)[0] + "\n}\n"
    agent = read("OpenAI/OpenAIAgentProvider.swift")
    methods = ["static func reasoningEffort(", "static func wireEffort(",
               "static func clampEffort(", "static func injectResponsesThinkingParams("]
    pure_types += "\nenum OpenAIAgentProvider {\n" + "\n".join(declaration(agent, m) for m in methods) + "\n}\n"
    api = read("OpenAI/OpenAIModelsAPI.swift")
    visibility = re.search(r"static let hiddenCodexVisibilities[^\n]+", api)[0]
    methods = ["static func isListedInCodexCatalog(", "static func codexCatalogSupportsReasoning(",
               "static func applyGPT62ReasoningMetadata(", "static func parseCodexModels(",
               "private static func mergeKeepingAuthoritative("]
    pure_types += "\nenum OpenAIModelsAPI {\n" + visibility + "\n"
    pure_types += "\n".join(declaration(api, m) for m in methods)
    # Accessibility glue only: the merge implementation remains byte-for-byte.
    pure_types += "\nstatic func smokeMerge(fresh: [LLMModel], enriched: [LLMModel]) -> [LLMModel] {\n"
    pure_types += "mergeKeepingAuthoritative(fresh: fresh, enriched: enriched)\n}\n}\n"
    return pure_types


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--static", action="store_true")
    mode.add_argument("--native", action="store_true")
    mode.add_argument("--emit-swift", type=Path)
    args = parser.parse_args()
    if args.emit_swift:
        args.emit_swift.write_text(extracted_swift())
        print("EXTRACTION ONLY: no Swift compilation or execution performed")
        return 0
    if args.native:
        compiler = shutil.which("swiftc")
        if compiler is None:
            print("SKIP: swiftc unavailable; no Swift/native test ran")
            return 77
        with tempfile.TemporaryDirectory(prefix="gpt62-smoke-") as temp:
            temp = Path(temp)
            source = temp / "Production.swift"
            source.write_text(extracted_swift())
            binary = temp / "gpt62-smoke"
            command = [compiler]
            if sys.platform == "darwin":
                sdk = subprocess.check_output(
                    ["xcrun", "--sdk", "macosx", "--show-sdk-path"],
                    text=True, timeout=30).strip()
                command += ["-sdk", sdk, "-target",
                            f"{platform.machine()}-apple-macosx15.0"]
            command += ["-parse-as-library", str(source),
                        str(ROOT / "tests/Standalone/GPT62ReasoningTests.swift"),
                        "-o", str(binary)]
            build = subprocess.run(command, timeout=120)
            if build.returncode:
                return build.returncode
            return subprocess.run([str(binary)], timeout=30).returncode
    print("STATIC / PYTHON REFERENCE ONLY: not Swift compilation, simulator or device evidence", flush=True)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(GPT62StaticChecks)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
