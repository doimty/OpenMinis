import SwiftUI

extension View {
    /// iOS 15's alert actions builder silently omits TextField controls. Keep
    /// the caller's original field/message builders and localized Text values;
    /// only the legacy presentation becomes a normal editable Form sheet.
    func compatTextInputAlert<Fields: View, Message: View>(
        _ title: Text,
        isPresented: Binding<Bool>,
        confirmLabel: Text,
        onConfirm: @escaping () -> Void,
        onCancel: @escaping () -> Void = {},
        @ViewBuilder fields: () -> Fields,
        @ViewBuilder message: () -> Message
    ) -> some View {
        modifier(CompatTextInputAlertModifier(
            title: title, isPresented: isPresented, confirmLabel: confirmLabel,
            onConfirm: onConfirm, onCancel: onCancel,
            fields: fields(), message: message()))
    }
}

private struct CompatTextInputAlertModifier<Fields: View, Message: View>: ViewModifier {
    let title: Text
    @Binding var isPresented: Bool
    let confirmLabel: Text
    let onConfirm: () -> Void
    let onCancel: () -> Void
    let fields: Fields
    let message: Message
    @State private var lifecycle = InputPromptLifecycle()

    init(title: Text, isPresented: Binding<Bool>, confirmLabel: Text,
         onConfirm: @escaping () -> Void, onCancel: @escaping () -> Void,
         fields: Fields, message: Message) {
        self.title = title
        self._isPresented = isPresented
        self.confirmLabel = confirmLabel
        self.onConfirm = onConfirm
        self.onCancel = onCancel
        self.fields = fields
        self.message = message
    }

    @ViewBuilder
    func body(content: Content) -> some View {
        if #available(iOS 16.0, *) {
            content.alert(title, isPresented: $isPresented) {
                fields
                Button(action: onConfirm) { confirmLabel }
                Button(role: .cancel, action: onCancel) { Text("Cancel") }
            } message: {
                message
            }
        } else {
            content.modifier(LegacyTextInputAlert(
                title: title, isPresented: $isPresented,
                lifecycle: $lifecycle, confirmLabel: confirmLabel,
                onConfirm: onConfirm, onCancel: onCancel,
                fields: fields, message: message))
        }
    }
}

/// Internal, separately testable legacy component. It owns only its input
/// sheet, never the parent browser/settings flow's environment dismiss action.
struct LegacyTextInputAlert<Fields: View, Message: View>: ViewModifier {
    let title: Text
    @Binding var isPresented: Bool
    @Binding var lifecycle: InputPromptLifecycle
    let confirmLabel: Text
    let onConfirm: () -> Void
    let onCancel: () -> Void
    let fields: Fields
    let message: Message

    private var sheetBinding: Binding<Bool> {
        Binding(
            get: { lifecycle.isPresented },
            set: { visible in
                if !visible { lifecycle.requestClose(.cancel) }
            })
    }

    func body(content: Content) -> some View {
        content
            .sheet(isPresented: sheetBinding, onDismiss: completeDismissal) {
                NavigationView {
                    Form {
                        Section {
                            fields
                        } footer: {
                            message
                        }
                    }
                    .navigationTitle(title)
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) {
                            Button { lifecycle.requestClose(.cancel) } label: { Text("Cancel") }
                                .accessibilityIdentifier("compat-input-cancel")
                        }
                        ToolbarItem(placement: .confirmationAction) {
                            Button { lifecycle.requestClose(.confirm) } label: { confirmLabel }
                                .accessibilityIdentifier("compat-input-confirm")
                        }
                    }
                }
                .navigationViewStyle(StackNavigationViewStyle())
            }
            .onAppear { lifecycle.synchronize(requested: isPresented) }
            .onChange(of: isPresented) { requested in
                lifecycle.synchronize(requested: requested)
            }
    }

    private func completeDismissal() {
        guard let completion = lifecycle.didDismiss() else { return }
        // UIKit has finished dismissing the sheet now. Commit editing and
        // run the existing action before clearing an optional-subject binding.
        // A follow-up collision alert can safely be presented by that action.
        switch completion {
        case .confirm: onConfirm()
        case .cancel: onCancel()
        case .external: break
        }
        isPresented = false
    }
}
