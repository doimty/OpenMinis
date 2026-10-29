/// Executes the production lifecycle model on the macOS host, without UIKit.
@main
struct InputPromptLifecycleTests {
    static func main() {
        var checks = 0
        func check(_ condition: Bool, _ message: String) {
            guard condition else { fatalError("FAIL: \(message)") }
            checks += 1
        }
        var state = InputPromptLifecycle()
        check(!state.isActive && state.pending == nil, "initially idle")
        check(!state.presentationRequested(byOwner: false), "closed owner stays closed")
        check(state.didDismiss(ownerRequested: false) == nil, "idle dismissal must not fabricate Cancel")
        state.requestClose(.confirm)
        check(state.pending == nil, "cannot submit before a presentation")
        check(state.presentationRequested(byOwner: true), "owner opens without an observer callback")
        state.didPresent()
        check(state.isActive, "actual sheet appearance marks activity")
        state.didPresent()
        check(state.isActive && state.pending == nil, "repeat appearance does not discard editing")
        state.requestClose(.confirm)
        check(!state.presentationRequested(byOwner: true) && state.pending == .confirm, "confirm waits for dismissal while retaining owner")
        state.requestClose(.cancel)
        check(state.pending == .confirm, "double action cannot replace first completion")
        check(state.didDismiss(ownerRequested: true) == .confirm, "confirmed dismissal dispatches once")
        check(state.didDismiss(ownerRequested: false) == nil, "second dismissal is a no-op")
        check(!state.isActive && state.pending == nil, "closed after consumption")

        // Regression from the native probe: no synchronize(false/true) call
        // between dismissal and reopening. SwiftUI may coalesce that edge.
        check(!state.presentationRequested(byOwner: false), "owner cleared after callback")
        check(state.presentationRequested(byOwner: true), "immediate reopen cannot be lost to observer coalescing")
        state.didPresent()
        state.requestClose(.cancel)
        check(state.didDismiss(ownerRequested: true) == .cancel, "Cancel/swipe is cancellation")
        check(state.didDismiss(ownerRequested: false) == nil, "cancellation is one-shot")

        state.didPresent()
        check(!state.presentationRequested(byOwner: false), "external revocation hides directly")
        check(state.didDismiss(ownerRequested: false) == .external, "external close never dispatches a user action")

        state.didPresent()
        state.requestClose(.confirm)
        // No intermediate observer synchronization: this is the reviewer’s
        // cancellation race, vetoed using the actual owner at completion.
        check(state.didDismiss(ownerRequested: false) == .external, "owner revoked before didDismiss vetoes stale save")
        state.didPresent()
        check(state.didDismiss(ownerRequested: true) == .cancel, "implicit UI dismissal cannot save")

        var saves = 0
        for _ in 0..<20 {
            check(state.presentationRequested(byOwner: true), "reopen directly from caller")
            state.didPresent()
            state.requestClose(.confirm)
            if state.didDismiss(ownerRequested: true) == .confirm { saves += 1 }
            check(state.didDismiss(ownerRequested: false) == nil, "no duplicate completion on reopen cycle")
        }
        check(saves == 20, "one save per confirmed presentation")
        print("PASS: \(checks) production input-prompt lifecycle checks")
    }
}
