//
//  ChatPDFText.swift
//  MinisApp
//
//  Foundation-only naming helpers for the transcript PDF. Kept apart from the
//  renderer so the filename/title rules can run in a host regression.
//

import Foundation

struct ChatPDFSection {
    let role: String
    let text: String
}

enum ChatPDFText {
    /// First non-empty line of the conversation, used as the document title
    /// and filename stem. Falls back when there is nothing usable.
    static func suggestedTitle(from sections: [ChatPDFSection], fallback: String) -> String {
        for section in sections {
            let line = section.text.split(whereSeparator: { $0 == "\n" || $0 == "\r" }).first
                .map(String.init)?
                .trimmingCharacters(in: .whitespaces) ?? ""
            if !line.isEmpty {
                return line.count > 48 ? String(line.prefix(48)) + "…" : line
            }
        }
        return fallback
    }

    /// Filesystem-safe name with a stable date stamp. Path separators, quotes
    /// and control characters are replaced; empty titles become "Conversation".
    static func suggestedFilename(title: String, date: Date = Date()) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        let stamp = formatter.string(from: date)
        var safe = title
        for bad in ["/", ":", "\\", "\n", "\r", "\t", "\"", "'"] {
            safe = safe.replacingOccurrences(of: bad, with: "-")
        }
        safe = safe.trimmingCharacters(in: .whitespacesAndNewlines)
        if safe.isEmpty { safe = "Conversation" }
        if safe.count > 60 { safe = String(safe.prefix(60)) }
        return "Minis \(safe) \(stamp).pdf"
    }
}
