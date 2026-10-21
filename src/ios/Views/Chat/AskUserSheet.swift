//
//  AskUserSheet.swift
//  MinisApp
//
//  The card shown while a tool awaits the user's answer.
//

import SwiftUI

struct AskUserSheet: View {
    let request: AskUserManager.Request

    @State private var freeText = ""
    @FocusState private var freeTextFocused: Bool

    private var prompt: AskUserPrompt { request.prompt }

    var body: some View {
        CompatNavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    if !prompt.header.isEmpty {
                        Text(prompt.header)
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(.secondary)
                            .textCase(.uppercase)
                    }
                    Text(prompt.question)
                        .font(.title3.weight(.semibold))
                        .fixedSize(horizontal: false, vertical: true)

                    if !prompt.options.isEmpty {
                        VStack(spacing: 10) {
                            ForEach(prompt.options, id: \.self) { option in
                                Button {
                                    AskUserManager.shared.respond(id: request.id, selected: option, freeText: nil)
                                } label: {
                                    Text(option)
                                        .font(.body)
                                        .frame(maxWidth: .infinity, alignment: .leading)
                                        .padding(.horizontal, 14)
                                        .padding(.vertical, 12)
                                        .background(Color(UIColor.secondarySystemBackground),
                                                    in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                                }
                                .buttonStyle(.plain)
                            }
                        }
                    }

                    if prompt.allowsFreeText {
                        VStack(alignment: .leading, spacing: 8) {
                            TextField(AppLocalized("Type an answer"), text: $freeText)
                                .textFieldStyle(.plain)
                                .padding(.horizontal, 14)
                                .padding(.vertical, 12)
                                .background(Color(UIColor.secondarySystemBackground),
                                            in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                                .focused($freeTextFocused)
                                .submitLabel(.send)
                                .onSubmit { sendFreeText() }
                            Button(action: sendFreeText) {
                                Text(AppLocalized("Send"))
                                    .font(.body.weight(.semibold))
                                    .frame(maxWidth: .infinity)
                                    .padding(.vertical, 10)
                            }
                            .buttonStyle(.borderedProminent)
                            .disabled(freeText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                        }
                    }

                    Button(role: .cancel) {
                        AskUserManager.shared.cancel(id: request.id)
                    } label: {
                        Text(AppLocalized("Skip"))
                            .frame(maxWidth: .infinity)
                    }
                    .padding(.top, 4)
                }
                .padding(20)
            }
            .navigationTitle(AppLocalized("Question"))
            .navigationBarTitleDisplayMode(.inline)
        }
        .interactiveDismissDisabled()
        .onAppear {
            if prompt.options.isEmpty { freeTextFocused = true }
        }
        // A sheet can still be torn down by the system (parent view replaced).
        // Cancelling here keeps the tool call from waiting on a card that no
        // longer exists; respond()/cancel() from the buttons make this a no-op.
        .onDisappear {
            AskUserManager.shared.cancel(id: request.id)
        }
    }

    private func sendFreeText() {
        let trimmed = freeText.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        AskUserManager.shared.respond(id: request.id, selected: nil, freeText: trimmed)
    }
}

/// Attaches the question card to a view that is always on screen while chat
/// tools run.
struct AskUserSheetModifier: ViewModifier {
    @ObservedObject private var manager = AskUserManager.shared

    /// The card cannot be dismissed by gesture, so the only way this binding
    /// is set to nil is `respond` / `cancel` already resolving it. Routing the
    /// setter through cancel keeps a system-driven teardown from stranding the
    /// tool call.
    private var pendingBinding: Binding<AskUserManager.Request?> {
        Binding(
            get: { manager.pending },
            set: { newValue in
                guard newValue == nil, let current = manager.pending else { return }
                manager.cancel(id: current.id)
            }
        )
    }

    func body(content: Content) -> some View {
        content.sheet(item: pendingBinding) { request in
            AskUserSheet(request: request)
        }
    }
}
