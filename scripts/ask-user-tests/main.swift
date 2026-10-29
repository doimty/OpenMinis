import Foundation

// Standalone native macOS regression harness; no app, no kernel, no UI.
private func expect(_ condition: @autoclosure () -> Bool, _ message: String) {
    if !condition() { fatalError("FAIL: \(message)") }
}

private func parse(_ q: String, _ h: String? = nil, _ o: String? = nil,
                   _ f: Bool? = nil) -> AskUserPrompt? {
    AskUserPrompt.parse(question: q, header: h, options: o, allowsFreeText: f)
}

private func testRejectsEmptyQuestion() {
    expect(parse("") == nil, "empty question rejected")
    expect(parse("   \n\t ") == nil, "whitespace question rejected")
}

private func testOptionParsing() {
    let prompt = parse("Which provider?",
                       "Provider",
                       "  OpenAI  \n- Anthropic\n2. Moonshot\n\n* Moonshot\n• Local model\n")
    expect(prompt != nil, "prompt parses")
    let p = prompt!
    expect(p.question == "Which provider?", "question trimmed")
    expect(p.header == "Provider", "header trimmed")
    expect(p.options == ["OpenAI", "Anthropic", "Moonshot", "Local model"],
           "bullets and numbering stripped, duplicates collapsed: \(p.options)")
    expect(p.allowsFreeText == false, "options with no flag default to no free text")
}

private func testFreeTextDefaults() {
    expect(parse("Pick", nil, nil, nil)!.allowsFreeText, "no options offers free text")
    expect(parse("Pick", nil, "a\nb", true)!.allowsFreeText, "explicit true wins")
    expect(parse("Pick", nil, "a\nb", false)!.allowsFreeText == false, "explicit false wins")
}

private func testOptionCap() {
    let many = (1...12).map { "option \($0)" }.joined(separator: "\n")
    let p = parse("Pick", nil, many)!
    expect(p.options.count == AskUserPrompt.maxOptions,
           "options capped at \(AskUserPrompt.maxOptions), got \(p.options.count)")
    expect(p.options.first == "option 1", "first option kept")
}

private func decode(_ json: String) -> [String: Any] {
    guard let data = json.data(using: .utf8),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
        fatalError("FAIL: result is not valid JSON: \(json)")
    }
    return obj
}

private func testResultJSON() {
    let p = parse("Which provider?", nil, "OpenAI\nAnthropic")!

    let selected = decode(p.resultJSON(selected: "OpenAI", freeText: nil))
    expect(selected["ok"] as? Bool == true, "ok true")
    expect(selected["selected"] as? String == "OpenAI", "selected carried")
    expect(selected["answer"] as? String == "OpenAI", "answer falls back to selection")
    expect(selected["free_text"] == nil, "absent free text omitted")

    let typed = decode(p.resultJSON(selected: nil, freeText: "  use the local one\n"))
    expect(typed["answer"] as? String == "use the local one", "free text trimmed and preferred")
    expect(typed["free_text"] as? String == "use the local one", "free text carried")

    let quoted = decode(p.resultJSON(selected: "he said \"hi\"\nnext line", freeText: nil))
    expect(quoted["selected"] as? String == "he said \"hi\"\nnext line",
           "quotes and newlines survive JSON escaping")

    let blank = decode(p.resultJSON(selected: nil, freeText: "   "))
    expect(blank["answer"] as? String == "", "blank free text is not an answer")
    expect(blank["free_text"] == nil, "blank free text omitted")
}

private func testCancelledJSON() {
    let obj = decode(AskUserPrompt.cancelledJSON)
    expect(obj["ok"] as? Bool == false, "cancelled is not ok")
    expect(obj["cancelled"] as? Bool == true, "cancelled flag set")
}

let tests: [(String, () -> Void)] = [
    ("empty question is rejected", testRejectsEmptyQuestion),
    ("options strip bullets and numbering", testOptionParsing),
    ("free text defaults", testFreeTextDefaults),
    ("option cap", testOptionCap),
    ("result JSON", testResultJSON),
    ("cancelled JSON", testCancelledJSON),
]
for (name, test) in tests {
    test()
    print("PASS: \(name)")
}
print("PASS: \(tests.count) native AskUserPrompt regression groups")
