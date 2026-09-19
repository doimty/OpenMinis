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
        check(!state.isPresented && state.pending == nil, "initially idle")
        state.synchronize(requested: false)
        check(state.didDismiss() == nil, "idle dismissal must not fabricate Cancel")
        state.synchronize(requested: true)
        check(state.isPresented, "owner opens sheet")
        state.synchronize(requested: true)
        check(state.isPresented && state.pending == nil, "refresh while editing keeps prompt open")
        state.requestClose(.confirm)
        check(!state.isPresented && state.pending == .confirm, "confirm waits for actual dismissal")
        state.requestClose(.cancel)
        check(state.pending == .confirm, "double action cannot replace first completion")
        state.synchronize(requested: true)
        check(!state.isPresented, "owner retained during dismissal must not reopen sheet")
        check(state.didDismiss() == .confirm, "confirmed dismissal dispatches once")
        check(state.didDismiss() == nil, "second dismissal is a no-op")
        check(state.pending == nil && !state.isPresented, "closed after consumption")

        state.synchronize(requested: true)
        state.requestClose(.cancel)
        check(state.didDismiss() == .cancel, "Cancel / interactive swipe is cancellation")
        check(state.didDismiss() == nil, "cancellation dispatched only once")

        state.synchronize(requested: true)
        state.synchronize(requested: false)
        check(!state.isPresented && state.pending == .external, "external owner cancellation dismisses")
        check(state.didDismiss() == .external, "external cancellation never becomes save/cancel action")

        state.synchronize(requested: true)
        state.requestClose(.confirm)
        state.synchronize(requested: false)
        check(state.didDismiss() == .external, "parent cancellation during closing vetoes stale save")

        state.synchronize(requested: true)
        check(state.didDismiss() == .cancel, "UI dismissal before binding callback still cannot save")
        check(state.didDismiss() == nil, "implicit cancellation also one-shot")

        var saves = 0
        for _ in 0..<20 {
            state.synchronize(requested: true)
            state.requestClose(.confirm)
            state.synchronize(requested: true)
            if state.didDismiss() == .confirm { saves += 1 }
            check(state.didDismiss() == nil, "no duplicate completion on reopen cycle")
        }
        check(saves == 20, "one save for every explicitly confirmed presentation")
        print("PASS: \(checks) production input-prompt lifecycle checks")
    }
}
