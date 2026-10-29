//
//  AskUserPrompt.swift
//  MinisApp
//
//  Parsing and result formatting for the ask_user tool.
//

import Foundation

/// One question the model wants a human answer to.
///
/// Pure: the manager, the sheet and the tool branch all consume this, and the
/// ordering/limit/JSON rules are exercised on a host.
struct AskUserPrompt: Equatable {
    let question: String
    let header: String
    let options: [String]
    let allowsFreeText: Bool

    static let maxOptions = 6
    /// Returned when the user dismisses the card or the turn is stopped.
    static let cancelledJSON = "{\"ok\":false,\"cancelled\":true}"

    /// Builds a prompt from raw tool arguments. `options` is newline separated
    /// (leading bullets / numbering are stripped). Free text is implied when
    /// no options were given at all — otherwise a question with only buttons
    /// could not be answered in the user's own words.
    static func parse(question: String,
                      header: String?,
                      options: String?,
                      allowsFreeText: Bool?) -> AskUserPrompt? {
        let trimmedQuestion = question.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmedQuestion.isEmpty else { return nil }

        var seen = Set<String>()
        var parsedOptions: [String] = []
        for rawLine in (options ?? "").split(whereSeparator: { $0 == "\n" || $0 == "\r" }) {
            let option = stripListMarker(String(rawLine)).trimmingCharacters(in: .whitespaces)
            guard !option.isEmpty else { continue }
            let folded = option.lowercased()
            guard !seen.contains(folded) else { continue }
            seen.insert(folded)
            parsedOptions.append(option)
            if parsedOptions.count == maxOptions { break }
        }

        return AskUserPrompt(
            question: trimmedQuestion,
            header: (header ?? "").trimmingCharacters(in: .whitespacesAndNewlines),
            options: parsedOptions,
            allowsFreeText: allowsFreeText ?? parsedOptions.isEmpty
        )
    }

    /// Result text handed back to the model as the tool output.
    func resultJSON(selected: String?, freeText: String?) -> String {
        let trimmedFree = freeText?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        var payload: [String: Any] = ["ok": true]
        if let selected, !selected.isEmpty { payload["selected"] = selected }
        if !trimmedFree.isEmpty { payload["free_text"] = trimmedFree }
        if !trimmedFree.isEmpty {
            payload["answer"] = trimmedFree
        } else {
            payload["answer"] = selected ?? ""
        }
        guard let data = try? JSONSerialization.data(withJSONObject: payload, options: [.sortedKeys]),
              let json = String(data: data, encoding: .utf8) else {
            return "{\"ok\":true}"
        }
        return json
    }

    /// "- item", "* item", "1. item", "2) item" all become "item".
    private static func stripListMarker(_ line: String) -> String {
        var text = line.trimmingCharacters(in: .whitespaces)
        for marker in ["- ", "* ", "• "] where text.hasPrefix(marker) {
            text.removeFirst(marker.count)
            return text
        }
        if let range = text.range(of: #"^\d+[.)]\s+"#, options: .regularExpression) {
            text.removeSubrange(range)
        }
        return text
    }
}
