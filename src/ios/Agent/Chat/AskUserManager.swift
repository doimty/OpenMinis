//
//  AskUserManager.swift
//  MinisApp
//
//  Presents one question at a time and resumes the tool call that asked it.
//

import Foundation
import Combine

/// Serialises the model's questions against the single card the UI can show.
///
/// The tool call awaits `ask(_:)`; the sheet calls `respond(...)` or `cancel(...)`.
/// Every path resumes the continuation exactly once (answer, cancel, timeout,
/// or task cancellation), so a stopped turn or a dismissed card can never leave
/// a tool call suspended for the rest of the app's life.
@MainActor
final class AskUserManager: ObservableObject {
    static let shared = AskUserManager()

    enum Outcome: Equatable {
        case answered(selected: String?, freeText: String?)
        case cancelled
        /// Another question is already on screen. The second caller is told so
        /// instead of silently replacing the first.
        case busy
    }

    struct Request: Identifiable {
        let id: UUID
        let prompt: AskUserPrompt
        fileprivate let continuation: CheckedContinuation<Outcome, Never>
    }

    @Published private(set) var pending: Request?

    private var timeoutTask: Task<Void, Never>?

    func ask(_ prompt: AskUserPrompt, timeout: TimeInterval = 600) async -> Outcome {
        if pending != nil { return .busy }
        let id = UUID()
        return await withTaskCancellationHandler(operation: {
            await withCheckedContinuation { (continuation: CheckedContinuation<Outcome, Never>) in
                // The task can already be cancelled here (turn stopped between
                // dispatch and presentation). Resuming immediately is the only
                // way to avoid waiting for a card nobody will ever see.
                if Task.isCancelled {
                    continuation.resume(returning: .cancelled)
                    return
                }
                pending = Request(id: id, prompt: prompt, continuation: continuation)
                timeoutTask = Task { [weak self] in
                    try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                    guard !Task.isCancelled else { return }
                    await MainActor.run { self?.cancel(id: id) }
                }
            }
        }, onCancel: {
            Task { @MainActor in self.cancel(id: id) }
        })
    }

    func respond(id: UUID, selected: String?, freeText: String?) {
        resolve(id: id, outcome: .answered(selected: selected, freeText: freeText))
    }

    func cancel(id: UUID) {
        resolve(id: id, outcome: .cancelled)
    }

    private func resolve(id: UUID, outcome: Outcome) {
        guard let request = pending, request.id == id else { return }
        pending = nil
        timeoutTask?.cancel()
        timeoutTask = nil
        request.continuation.resume(returning: outcome)
    }
}
