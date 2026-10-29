import Foundation

private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError("FAIL: \(message)") }
}

private func testTitle() {
    let sections = [
        ChatPDFSection(role: "You", text: "\n\n  First line of the ask  \nsecond line"),
        ChatPDFSection(role: "Assistant", text: "reply"),
    ]
    expect(ChatPDFText.suggestedTitle(from: sections, fallback: "fallback")
           == "First line of the ask", "first non-empty line wins and is trimmed")
    expect(ChatPDFText.suggestedTitle(from: [], fallback: "fallback") == "fallback",
           "empty input falls back")
    let blank = [ChatPDFSection(role: "System", text: "   \n\t")]
    expect(ChatPDFText.suggestedTitle(from: blank, fallback: "fallback") == "fallback",
           "whitespace-only text falls back")

    let long = String(repeating: "a", count: 60)
    let truncated = ChatPDFText.suggestedTitle(from: [ChatPDFSection(role: "You", text: long)],
                                              fallback: "x")
    expect(truncated.count == 49 && truncated.hasSuffix("…"),
           "long titles are cut to 48 characters plus an ellipsis")
}

private func testFilename() {
    let date = Date(timeIntervalSince1970: 1_700_000_000)
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.dateFormat = "yyyy-MM-dd"
    let stamp = formatter.string(from: date)

    let name = ChatPDFText.suggestedFilename(title: "a/b:c\\d\"e'f\ng", date: date)
    expect(name == "Minis a-b-c-d-e-f-g \(stamp).pdf", "unsafe characters replaced: \(name)")

    let empty = ChatPDFText.suggestedFilename(title: "   ", date: date)
    expect(empty == "Minis Conversation \(stamp).pdf", "empty title becomes Conversation")

    let long = ChatPDFText.suggestedFilename(title: String(repeating: "x", count: 90), date: date)
    let stem = String(long.dropLast(stamp.count + 5)) // "Minis " + truncated title
    expect(stem.count == 66, "long filenames are bounded: \(long)")
    expect(long.hasSuffix(" \(stamp).pdf"), "filename keeps the date stamp")
}

let tests: [(String, () -> Void)] = [
    ("title selection", testTitle),
    ("filename sanitising", testFilename),
]
for (name, test) in tests {
    test()
    print("PASS: \(name)")
}
print("PASS: \(tests.count) native ChatPDFText regression groups")
