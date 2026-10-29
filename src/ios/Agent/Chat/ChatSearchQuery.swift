//
//  ChatSearchQuery.swift
//  MinisApp
//
//  Keyword parsing and match ranges for in-conversation search.
//

import Foundation

/// A parsed in-conversation search query.
///
/// Pure so the ordering, de-duplication and range-merge rules can be exercised
/// on a host: the search sheet itself only renders what this produces, and the
/// SQLite query receives exactly `keywords`.
struct ChatSearchQuery: Equatable {
    /// Whitespace-separated terms, case-insensitively de-duplicated, capped so
    /// a pasted paragraph cannot turn into a hundred LIKE clauses.
    let keywords: [String]

    static let maxKeywords = 6

    var isEmpty: Bool { keywords.isEmpty }

    static func parse(_ raw: String, maxKeywords: Int = ChatSearchQuery.maxKeywords) -> ChatSearchQuery {
        guard maxKeywords > 0 else { return ChatSearchQuery(keywords: []) }
        var seen = Set<String>()
        var ordered: [String] = []
        for token in raw.split(whereSeparator: { $0.isWhitespace }) {
            let term = String(token)
            let folded = term.lowercased()
            guard !folded.isEmpty, !seen.contains(folded) else { continue }
            seen.insert(folded)
            ordered.append(term)
            if ordered.count == maxKeywords { break }
        }
        return ChatSearchQuery(keywords: ordered)
    }

    /// Ranges of `text` that match any keyword, sorted and merged.
    ///
    /// Case folding is applied by `range(of:options:.caseInsensitive)` on the
    /// original string: indices derived from a `lowercased()` copy are invalid
    /// once folding changes length (Turkish "İ"), which traps on substring.
    static func highlightRanges(in text: String, keywords: [String]) -> [Range<String.Index>] {
        guard !text.isEmpty, !keywords.isEmpty else { return [] }
        var found: [Range<String.Index>] = []
        for keyword in keywords where !keyword.isEmpty {
            var cursor = text.startIndex
            while cursor < text.endIndex,
                  let match = text.range(of: keyword,
                                         options: [.caseInsensitive],
                                         range: cursor..<text.endIndex) {
                found.append(match)
                cursor = match.upperBound
            }
        }
        guard found.count > 1 else { return found }
        let sorted = found.sorted { $0.lowerBound < $1.lowerBound }
        var merged: [Range<String.Index>] = []
        for range in sorted {
            if let last = merged.last, range.lowerBound <= last.upperBound {
                merged[merged.count - 1] = last.lowerBound..<max(last.upperBound, range.upperBound)
            } else {
                merged.append(range)
            }
        }
        return merged
    }

    /// Search snippets come out of `parts_json`, so they still carry newlines
    /// and runs of indentation. Collapse them for a one-to-three line row.
    static func normalizedSnippet(_ text: String) -> String {
        let collapsed = text
            .replacingOccurrences(of: "\r\n", with: "\n")
            .split(whereSeparator: { $0 == "\n" || $0 == "\t" })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
            .joined(separator: " ")
        return collapsed
    }
}
