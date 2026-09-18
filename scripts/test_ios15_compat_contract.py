#!/usr/bin/env python3
"""Narrow structural guards, not a substitute for the Apple-compiler smoke."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPAT = ROOT / "src/ios/Shared/SwiftUICompatibility.swift"


class CompatibilityContractTests(unittest.TestCase):
    def test_range_line_limit_adapter_has_one_owner(self):
        source = COMPAT.read_text()
        definitions = re.findall(r"\bfunc\s+compatLineLimit\s*\(", source)
        self.assertEqual(len(definitions), 1, "duplicate Swift declarations are not syntax-tree errors")

    def test_visibility_signature_does_not_leak_newer_sdk_type(self):
        source = COMPAT.read_text()
        signatures = re.findall(r"func\s+compat\w+[^\{]+\{", source)
        self.assertFalse(any("ToolbarPlacement" in signature for signature in signatures),
                         "a body availability guard does not protect the signature")

    def test_legacy_vertical_editor_is_not_a_single_line_text_field(self):
        source = COMPAT.read_text()
        self.assertIn("TextEditor(text:", source)
        self.assertNotIn("func compatTextFieldAxis", source,
                         "retire the helper that silently drops the vertical axis")

    def test_chat_and_voice_timers_do_not_require_duration_clock(self):
        hits = []
        for directory in ("Providers/Voice", "Views/Chat"):
            for path in (ROOT / "src/ios" / directory).rglob("*.swift"):
                if re.search(r"Task\s*\.\s*sleep\s*\(\s*(?:for|until)\s*:", path.read_text()):
                    hits.append(str(path.relative_to(ROOT)))
        self.assertEqual(hits, [], "use the project's iOS-15-safe nanosecond timers")

    def test_millisecond_conversion_keeps_intervals_and_cancellation_guards(self):
        cases = (
            ("Providers/Voice/VoiceProviderResolver.swift", "200_000_000", 1),
            ("Views/Chat/Voice/SpeechPlayerControl.swift", "250_000_000", 1),
            ("Views/Chat/Voice/SpeechPlayerControl.swift", "UInt64(Self.descentSettleMillis) * 1_000_000", 1),
            ("Views/Chat/Voice/SpeechPlayerControl.swift", "150_000_000", 1),
            ("Views/Chat/AIChatView.swift", "900_000_000", 1),
            ("Views/Chat/AIChatView.swift", "380_000_000", 2),
            ("Views/Chat/AIChatView.swift", "320_000_000", 1),
        )
        for relative, duration, expected in cases:
            with self.subTest(file=relative, duration=duration):
                source = (ROOT / "src/ios" / relative).read_text()
                call = f"try? await Task.sleep(nanoseconds: {duration})"
                guarded = re.escape(call) + r"\s*guard !Task\.isCancelled"
                self.assertEqual(len(re.findall(guarded, source)), expected)
        player = (ROOT / "src/ios/Views/Chat/Voice/SpeechPlayerControl.swift").read_text()
        self.assertRegex(player, r"descentSettleMillis\s*=\s*1200\b")

    def test_automatic_punctuation_is_optional_on_ios15(self):
        source = (ROOT / "src/ios/Providers/Voice/VoiceProvider+System.swift").read_text()
        self.assertRegex(source, r"if #available\(iOS 16\.0, \*\) \{\s*"
                                 r"recognitionRequest\.addsPunctuation = true\s*\}")
        self.assertIn("recognitionRequest.taskHint = .dictation", source)
        self.assertIn("recognizer.recognitionTask(with: recognitionRequest)", source)


if __name__ == "__main__":
    unittest.main()
