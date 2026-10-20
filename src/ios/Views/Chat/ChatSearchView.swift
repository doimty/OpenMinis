//
//  ChatSearchView.swift
//  MinisApp
//
//  In-conversation message search.
//

import SwiftUI

/// Search the messages of the conversation currently on screen.
///
/// The backend (`ChatStore.searchMessages`) already existed for the sessions
/// CLI; this is the missing UI: one search field, keyword snippets with the
/// match highlighted, and a jump back into the transcript.
struct ChatSearchView: View {
    let sessionId: String
    /// Called with the message the user picked. The chat view scrolls to it
    /// and flashes the row.
    let onSelect: (UUID) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var query = ""
    @State private var hits: [Hit] = []
    @State private var isSearching = false
    @State private var searchTask: Task<Void, Never>?

    private struct Hit: Identifiable {
        let id: String
        let messageId: UUID
        let role: String
        let createdAt: Date
        let snippet: String
    }

    var body: some View {
        CompatNavigationStack {
            List {
                if parsed.isEmpty {
                    Section {
                        Text(AppLocalized("Search this conversation. Separate words to require all of them."))
                            .font(.system(size: 14))
                            .foregroundStyle(Color(UIColor.secondaryLabel))
                    }
                } else if isSearching && hits.isEmpty {
                    Section {
                        HStack(spacing: 8) {
                            ProgressView()
                            Text(AppLocalized("Searching…"))
                                .font(.system(size: 14))
                                .foregroundStyle(Color(UIColor.secondaryLabel))
                        }
                    }
                } else if hits.isEmpty {
                    Section {
                        Text(AppLocalized("No matches"))
                            .font(.system(size: 14))
                            .foregroundStyle(Color(UIColor.secondaryLabel))
                    }
                } else {
                    Section {
                        ForEach(hits) { hit in
                            Button {
                                onSelect(hit.messageId)
                                dismiss()
                            } label: {
                                row(hit)
                            }
                            .buttonStyle(.plain)
                        }
                    } header: {
                        Text(String(format: AppLocalized("%d matches"), hits.count))
                    }
                }
            }
            .listStyle(.plain)
            .searchable(text: $query, prompt: Text(AppLocalized("Search this chat")))
            .onChange(of: query) { _ in scheduleSearch() }
            .navigationTitle(AppLocalized("Search"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button(AppLocalized("Cancel")) { dismiss() }
                }
            }
        }
        .onDisappear { searchTask?.cancel() }
    }

    // MARK: - Row

    private func row(_ hit: Hit) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(hit.role == "user" ? AppLocalized("You") : AppLocalized("Minis"))
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(Color(UIColor.secondaryLabel))
                Spacer(minLength: 4)
                Text(Self.relative(hit.createdAt))
                    .font(.system(size: 12))
                    .foregroundStyle(Color(UIColor.tertiaryLabel))
            }
            highlighted(hit.snippet)
                .font(.system(size: 14))
                .foregroundStyle(Color(UIColor.label))
                .lineLimit(3)
        }
        .padding(.vertical, 2)
        .contentShape(Rectangle())
    }

    private func highlighted(_ text: String) -> Text {
        let ranges = ChatSearchQuery.highlightRanges(in: text, keywords: parsed.keywords)
        guard !ranges.isEmpty else { return Text(text) }
        var result = Text("")
        var cursor = text.startIndex
        for range in ranges {
            if cursor < range.lowerBound {
                result = result + Text(String(text[cursor..<range.lowerBound]))
            }
            result = result + Text(String(text[range]))
                .bold()
                .foregroundColor(.accentColor)
            cursor = range.upperBound
        }
        if cursor < text.endIndex {
            result = result + Text(String(text[cursor...]))
        }
        return result
    }

    private static func relative(_ date: Date) -> String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    // MARK: - Search

    private var parsed: ChatSearchQuery {
        ChatSearchQuery.parse(query)
    }

    private func scheduleSearch() {
        searchTask?.cancel()
        let parsed = self.parsed
        guard !parsed.isEmpty else {
            hits = []
            isSearching = false
            return
        }
        isSearching = true
        let sessionId = self.sessionId
        searchTask = Task {
            // Coalesce keystrokes; the LIKE scan runs on the ChatStore actor
            // and must not queue a query per character.
            try? await Task.sleep(nanoseconds: 250_000_000)
            guard !Task.isCancelled else { return }
            let rows = await ChatStore.shared.searchMessages(
                sessionIds: [sessionId],
                keywords: parsed.keywords,
                limit: 100
            )
            guard !Task.isCancelled else { return }
            let mapped = rows.compactMap { row -> Hit? in
                guard let id = UUID(uuidString: row.messageId) else { return nil }
                return Hit(id: row.messageId,
                           messageId: id,
                           role: row.role,
                           createdAt: row.createdAt,
                           snippet: ChatSearchQuery.normalizedSnippet(row.snippet))
            }
            hits = mapped
            isSearching = false
        }
    }
}
