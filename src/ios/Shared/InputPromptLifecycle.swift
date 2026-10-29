/// Completion state for the legacy input sheet. Presentation is derived
/// directly from the caller, never mirrored through an onChange observer:
/// a quick close/reopen can coalesce false -> true and lose that observer edge.
struct InputPromptLifecycle: Equatable {
    enum Completion: Equatable {
        case confirm
        case cancel
        case external
    }

    private(set) var isActive = false
    private(set) var pending: Completion?

    init() {}

    func presentationRequested(byOwner requested: Bool) -> Bool {
        requested && pending == nil
    }

    mutating func didPresent() {
        if pending == nil { isActive = true }
    }

    mutating func requestClose(_ completion: Completion) {
        guard isActive, pending == nil else { return }
        pending = completion
    }

    mutating func didDismiss(ownerRequested: Bool) -> Completion? {
        let completion = pending ?? (isActive ? .cancel : nil)
        isActive = false
        pending = nil
        // Recheck the actual owner at completion, not a delayed observation.
        // Revocation during dismissal must veto a previously requested save.
        if !ownerRequested, completion != nil { return .external }
        return completion
    }
}
