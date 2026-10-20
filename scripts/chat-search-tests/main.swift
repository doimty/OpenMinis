import Foundation

// Host regression for the in-conversation search query. Pure Foundation; no app,
// database or main run loop.
private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError("FAIL: \(message)") }
}

private func testParseSplitsAndDropsBlanks() {
    let q = ChatSearchQuery.parse("  swift\t  actor \n\n main actor ")
    expect(q.keywords == ["swift", "actor", "main"], "whitespace split keeps order, drops duplicates: \(q.keywords)")
    expect(!q.isEmpty, "non-empty query")
}

private func testParseDeduplicatesCaseInsensitively() {
    let q = ChatSearchQuery.parse("SQLite sqlite SQLITE")
    expect(q.keywords == ["SQLite"], "case-insensitive de-duplication keeps the first spelling: \(q.keywords)")
}

private func testParseCapsKeywordCount() {
    let raw = "a b c d e f g h i"
    expect(ChatSearchQuery.parse(raw, maxKeywords: 3).keywords == ["a", "b", "c"], "cap keeps the first terms")
    expect(ChatSearchQuery.parse(raw).keywords.count == ChatSearchQuery.maxKeywords, "default cap applies")
    expect(ChatSearchQuery.parse(raw, maxKeywords: 0).isEmpty, "zero cap yields no keywords")
}

private func testParseEmpty() {
    expect(ChatSearchQuery.parse("   \n\t ").isEmpty, "blank input is empty")
    expect(ChatSearchQuery.parse("").isEmpty, "empty input is empty")
}

private func testHighlightFindsAllOccurrences() {
    let text = "the Swift actor and the swift actor"
    let ranges = ChatSearchQuery.highlightRanges(in: text, keywords: ["swift"])
    expect(ranges.count == 2, "two case-insensitive matches, got \(ranges.count)")
    expect(ranges.allSatisfy { text[$0].lowercased() == "swift" }, "every range is the keyword")
}

private func testHighlightMergesOverlaps() {
    let text = "abcdef"
    let ranges = ChatSearchQuery.highlightRanges(in: text, keywords: ["abcd", "cdef"])
    expect(ranges.count == 1, "overlapping matches merge, got \(ranges.count)")
    expect(ranges[0].lowerBound == text.startIndex && ranges[0].upperBound == text.endIndex, "merged range spans both")
}

private func testHighlightCoalescesAdjacent() {
    // "aa" twice over "aaaa": the two occurrences touch, so they merge into one
    // run. Visually identical to two adjacent spans, and it keeps the renderer
    // from emitting a zero-width seam between them.
    let text = "aaaa"
    let ranges = ChatSearchQuery.highlightRanges(in: text, keywords: ["aa"])
    expect(ranges.count == 1, "adjacent occurrences coalesce, got \(ranges.count)")
    expect(String(text[ranges[0]]) == "aaaa", "coalesced span covers the whole run")
    let spaced = ChatSearchQuery.highlightRanges(in: "aa aa", keywords: ["aa"])
    expect(spaced.count == 2, "separated occurrences stay separate, got \(spaced.count)")
}

private func testHighlightSurvivesCaseFoldingLengthChanges() {
    // Turkish "İ" lowercases to two scalars; deriving indices from a lowercased
    // copy would produce out-of-bounds ranges and trap on substring.
    let text = "İstanbul trip"
    let ranges = ChatSearchQuery.highlightRanges(in: text, keywords: ["istanbul"])
    expect(ranges.isEmpty, "no false match across a folding length change")
    let exact = ChatSearchQuery.highlightRanges(in: text, keywords: ["İstanbul"])
    expect(exact.count == 1 && String(text[exact[0]]) == "İstanbul", "exact keyword still matches")
}

private func testHighlightEmptyInputs() {
    expect(ChatSearchQuery.highlightRanges(in: "", keywords: ["x"]).isEmpty, "empty text has no ranges")
    expect(ChatSearchQuery.highlightRanges(in: "text", keywords: []).isEmpty, "no keywords, no ranges")
    expect(ChatSearchQuery.highlightRanges(in: "text", keywords: [""]).isEmpty, "empty keyword ignored")
}

private func testSnippetNormalization() {
    let raw = "first line\r\n\tsecond   line\n\n   third  \n"
    expect(ChatSearchQuery.normalizedSnippet(raw) == "first line second   line third",
           "newlines and tabs collapse to single spaces: \(ChatSearchQuery.normalizedSnippet(raw))")
    expect(ChatSearchQuery.normalizedSnippet("\n \n").isEmpty, "whitespace-only snippet is empty")
}

private func testRangesMapBackToOriginalText() {
    let text = "Mixed CASE Swift"
    for range in ChatSearchQuery.highlightRanges(in: text, keywords: ["case", "swift"]) {
        expect(!String(text[range]).isEmpty, "range is a valid substring slice")
    }
}

let tests: [(String, () -> Void)] = [
    ("split drops blanks", testParseSplitsAndDropsBlanks),
    ("case-insensitive de-duplication", testParseDeduplicatesCaseInsensitively),
    ("keyword cap", testParseCapsKeywordCount),
    ("blank input", testParseEmpty),
    ("all occurrences", testHighlightFindsAllOccurrences),
    ("overlap merge", testHighlightMergesOverlaps),
    ("adjacent repeats coalesce", testHighlightCoalescesAdjacent),
    ("case folding length change", testHighlightSurvivesCaseFoldingLengthChanges),
    ("empty inputs", testHighlightEmptyInputs),
    ("snippet normalization", testSnippetNormalization),
    ("ranges map back", testRangesMapBackToOriginalText)
]

for (name, test) in tests {
    test()
    print("PASS: \(name)")
}
print("PASS: \(tests.count) native ChatSearchQuery regression groups")
