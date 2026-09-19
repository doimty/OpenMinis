/// The legacy input sheet has its own presentation state. Do not bind it
/// directly to the caller: an optional-subject binding can discard the item
/// before the confirm callback gets a chance to read it.
struct InputPromptLifecycle: Equatable {
    enum Completion: Equatable {
        case confirm
        case cancel
        case external
    }

    private(set) var isPresented = false
    private(set) var pending: Completion?

    init() {}

    mutating func synchronize(requested: Bool) {
        if requested {
            // The owner stays true until completion. Re-rendering while the
            // sheet dismisses must not immediately reopen that same sheet.
            if pending == nil { isPresented = true }
        } else if isPresented || pending != nil {
            isPresented = false
            pending = .external
        }
    }

    mutating func requestClose(_ completion: Completion) {
        guard isPresented else { return }
        pending = completion
        isPresented = false
    }

    mutating func didDismiss() -> Completion? {
        let completion = pending ?? (isPresented ? .cancel : nil)
        isPresented = false
        pending = nil
        return completion
    }
}
