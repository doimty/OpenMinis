// KeywordSnippetTests.swift
//
// [T-search-snippet-index] Standalone regression for the chat-search snippet
// index crash captured on the user's device (iPhone14,3 / iOS 15.1.1):
//
//   IPS  shared-8EEAC71D_Minis-2026-10-09-195808.ips
//        sha256 7d6b466ae7aadfb490065bea3a3e46049568c5eee4df2589b2373dbe10005676
//        EXC_CRASH / SIGTRAP at
//        String.index(after:) <- String.distance(from:to:) <- ChatStore.keywordSnippet
//
// Released defect: keywordSnippet found the keyword range in
// `lower = text.lowercased()` and then measured it with
// `text.distance(from: text.startIndex, to: range.lowerBound)` — an index
// owned by a different string. Once case folding changes lengths (the Turkish
// "İ" U+0130 expands to "i" + U+0307), the walk outruns `text.endIndex` and
// libswiftCore raises "String index is out of bounds" -> process trap.
//
// This file does NOT embed a copy of the production function: the runner
// scripts/test_keyword_snippet.py extracts the byte-exact slice of
// keywordSnippet() from src/ios/Agent/Chat/ChatStore.swift and wraps it in a
// minimal `ChatStore` stub (single documented token transform: `private
// static` -> `static`, so a separate test file can call it):
//
//   swiftc -parse-as-library <slice+wrapper>.swift KeywordSnippetTests.swift -o runner
//   ./runner            # exit 0 = all executed checks pass, 1 = FAIL
//
// The İ/KELVIN cases are the determinism anchors: with the released code the
// folded-space index lands past `text.endIndex` (each İ shifts the match one
// byte further than the source walk can reach), so the old pair traps before
// reaching SUMMARY.
//
// Output contract (one line per assertion):
//   PASS[<check-id>] / FAIL[<check-id>] <detail>
// followed by `SUMMARY pass=<n> fail=<n> skip=<n>`.
// Check ids are stable: the python mutation harness matches FAIL[<id>].
import Foundation
#if canImport(Glibc)
import Glibc
#endif

// MARK: - Harness

private final class SnippetHarness {
    private(set) var pass = 0
    private(set) var fail = 0
    private(set) var skip = 0

    func expect(_ id: String, _ condition: Bool, _ detail: @autoclosure () -> String = "") {
        if condition {
            pass += 1
            print("PASS[\(id)]")
        } else {
            fail += 1
            let d = detail()
            print("FAIL[\(id)]\(d.isEmpty ? "" : " " + d)")
        }
    }

    func expectEq(_ id: String, _ actual: String, _ expected: String) {
        if actual == expected {
            pass += 1
            print("PASS[\(id)]")
        } else {
            fail += 1
            print("FAIL[\(id)] expected \(expected.count) chars, actual \(actual.count) chars")
        }
    }
}

// MARK: - Cases

private func runSnippetChecks(_ h: SnippetHarness) {
    // Whole-text windows: text shorter than maxLength must come back verbatim.
    do {
        let text = "hello world needle tail"
        let s = ChatStore.keywordSnippet(from: text, keywords: ["needle"], maxLength: 600)
        h.expectEq("ascii_whole_text", s, text)
    }

    // Case-insensitive centering: the keyword differs in case from the hit.
    do {
        let text = String(repeating: "x", count: 100) + "NEEDLE" + String(repeating: "y", count: 100)
        let s = ChatStore.keywordSnippet(from: text, keywords: ["needle"], maxLength: 60)
        h.expect("case_insensitive_centered", s.contains("NEEDLE"), "len=\(s.count)")
    }

    // Deterministic trapper of the released defect: 20 İ expand the folded
    // copy by +20 bytes before the match, so the old folded-space index walked
    // past text.endIndex -> "String index is out of bounds".
    do {
        let text = String(repeating: "\u{0130}", count: 20) + " needle"
        let s = ChatStore.keywordSnippet(from: text, keywords: ["needle"], maxLength: 600)
        h.expectEq("turkish_dotted_i_traps_old_defect", s, text)
    }

    // Same shape in a natural sentence (multiple İ before the match).
    do {
        let text = "İstanbul'da İzmir'de İskenderun'da needle aradım"
        let s = ChatStore.keywordSnippet(from: text, keywords: ["needle"], maxLength: 600)
        h.expectEq("turkish_mixed_sentence", s, text)
    }

    // U+212A KELVIN SIGN folds to "k" (3 bytes -> 1): contractions must not
    // trap either.
    do {
        let text = "\u{212A}x"
        let s = ChatStore.keywordSnippet(from: text, keywords: ["x"], maxLength: 600)
        h.expectEq("kelvin_sign_contraction", s, text)
    }

    // Grapheme-cluster boundaries near the window edges must not split.
    do {
        let text = "🎉🎉🎉 needle 🎉🎉"
        let s = ChatStore.keywordSnippet(from: text, keywords: ["needle"], maxLength: 600)
        h.expectEq("emoji_boundaries_whole_text", s, text)
    }

    // Full window math for text > maxLength: exact slice + both ellipses.
    do {
        let text = String(repeating: "a", count: 1000) + "needle" + String(repeating: "b", count: 1000)
        let s = ChatStore.keywordSnippet(from: text, keywords: ["needle"], maxLength: 600)
        var expected = "…"
        expected += String(repeating: "a", count: 300)
        expected += "needle"
        expected += String(repeating: "b", count: 294)
        expected += "…"
        h.expectEq("window_exact_slice_and_ellipses", s, expected)
    }

    // No match: unchanged head behavior, no ellipsis.
    do {
        let text = String(repeating: "ab", count: 500)
        let s = ChatStore.keywordSnippet(from: text, keywords: ["zzz"], maxLength: 600)
        h.expectEq("no_match_returns_head", s, String(text.prefix(600)))
    }

    // First keyword absent: the loop must continue to later keywords.
    do {
        let text = "aaaa needle bbbb"
        let s = ChatStore.keywordSnippet(from: text, keywords: ["absent", "needle"], maxLength: 600)
        h.expectEq("first_keyword_absent_second_used", s, text)
    }

    // Heaviest trap shape: 40 İ before a match near the end (folded copy ends
    // +40 bytes past the source string's end).
    do {
        let text = String(repeating: "\u{0130}", count: 40) + " x"
        let s = ChatStore.keywordSnippet(from: text, keywords: ["x"], maxLength: 600)
        h.expectEq("turkish_40_expansions_past_end", s, text)
    }
}

@main
struct KeywordSnippetTestsMain {
    static func main() {
        let h = SnippetHarness()
        runSnippetChecks(h)
        print("SUMMARY pass=\(h.pass) fail=\(h.fail) skip=\(h.skip)")
        exit(h.fail == 0 ? 0 : 1)
    }
}
