import SwiftUI

/// Native navigation on iOS 16+, stacked NavigationView on iOS 15.
/// This adapter is only for stacks without a programmatic path.
struct CompatNavigationStack<Content: View>: View {
    private let content: Content

    init(@ViewBuilder content: () -> Content) {
        self.content = content()
    }

    var body: some View {
        if #available(iOS 16.0, *) {
            NavigationStack { content }
        } else {
            NavigationView { content }
                .navigationViewStyle(StackNavigationViewStyle())
        }
    }
}

/// Keep literal localization keys distinct from already-localized/dynamic
/// strings. Content can be an interactive field, not just a read-only value.
struct CompatLabeledContent<Content: View, Label: View>: View {
    private let label: Label
    private let content: Content
    private let combinesAccessibility: Bool

    init(@ViewBuilder content: () -> Content, @ViewBuilder label: () -> Label) {
        self.label = label()
        self.content = content()
        self.combinesAccessibility = false
    }

    var body: some View {
        if #available(iOS 16.0, *) {
            LabeledContent { content } label: { label }
        } else {
            HStack(alignment: .firstTextBaseline) {
                label
                Spacer(minLength: 16)
                content
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.trailing)
            }
            // Never hide a TextField, Button, or other interactive child.
            .accessibilityElement(children: combinesAccessibility ? .combine : .contain)
        }
    }
}

extension CompatLabeledContent where Label == Text {
    init(_ title: LocalizedStringKey, @ViewBuilder content: () -> Content) {
        self.label = Text(title)
        self.content = content()
        self.combinesAccessibility = false
    }

    init<S: StringProtocol>(_ title: S, @ViewBuilder content: () -> Content) {
        self.label = Text(verbatim: String(title))
        self.content = content()
        self.combinesAccessibility = false
    }
}

extension CompatLabeledContent where Content == Text, Label == Text {
    init(_ title: LocalizedStringKey, value: String) {
        self.label = Text(title)
        self.content = Text(verbatim: value)
        self.combinesAccessibility = true
    }

    init<S: StringProtocol>(_ title: S, value: String) {
        self.label = Text(verbatim: String(title))
        self.content = Text(verbatim: value)
        self.combinesAccessibility = true
    }
}

/// Own the old-system-visible value type instead of shadowing Apple's type
/// or leaking PresentationDetent through an iOS-15-visible signature.
enum CompatPresentationDetent: Hashable {
    case medium
    case large
    case fraction(CGFloat)
    case height(CGFloat)

    @available(iOS 16.0, *)
    var native: PresentationDetent {
        switch self {
        case .medium: return .medium
        case .large: return .large
        case .fraction(let fraction): return .fraction(fraction)
        case .height(let height): return .height(height)
        }
    }
}

extension View {
    /// Older TextField/Text views can cap lines, but cannot reserve a minimum
    /// number of lines. Preserve the maximum without exposing a newer overload.
    @ViewBuilder
    func compatLineLimit(_ range: ClosedRange<Int>) -> some View {
        if #available(iOS 16.0, *) { lineLimit(range) }
        else { lineLimit(range.upperBound) }
    }

    @ViewBuilder
    func compatVisibleScrollIndicators() -> some View {
        if #available(iOS 16.0, *) { scrollIndicators(.visible) }
        else { self } // Visible indicators are the iOS 15 default.
    }

    @ViewBuilder
    func compatNavigationBarBackground<S: ShapeStyle>(_ style: S) -> some View {
        if #available(iOS 16.0, *) { toolbarBackground(style, for: .navigationBar) }
        else { self }
    }

    @ViewBuilder
    func compatVisibleNavigationBarBackground() -> some View {
        if #available(iOS 16.0, *) { toolbarBackground(.visible, for: .navigationBar) }
        else { self }
    }

    /// The invisible value link used by a native List does not make the
    /// whole legacy row actionable. Install the tap only on the old platform.
    @ViewBuilder
    func compatLegacyNavigationTap(perform action: @escaping () -> Void) -> some View {
        if #available(iOS 16.0, *) { self }
        else { contentShape(Rectangle()).onTapGesture(perform: action) }
    }

    @ViewBuilder
    func compatHiddenScrollBackground() -> some View {
        if #available(iOS 16.0, *) {
            self.scrollContentBackground(.hidden)
        } else {
            self
        }
    }

    /// iOS 15 keeps a standard system sheet. The caller still owns dismissal,
    /// cancellation and transfer state; only unsupported presentation is omitted.
    @ViewBuilder
    func compatPresentationDetents(_ detents: Set<CompatPresentationDetent>) -> some View {
        if #available(iOS 16.0, *) {
            self.presentationDetents(Set(detents.map(\.native)))
        } else {
            self
        }
    }

    func compatPresentationDetentsMediumLarge() -> some View {
        compatPresentationDetents([.medium, .large])
    }

    func compatPresentationDetentsFraction75() -> some View {
        compatPresentationDetents([.fraction(0.75)])
    }

    func compatPresentationDetentHeight(_ height: CGFloat) -> some View {
        compatPresentationDetents([.height(height)])
    }

    @ViewBuilder
    func compatPresentationDragIndicator(_ visibility: Visibility) -> some View {
        if #available(iOS 16.0, *) {
            self.presentationDragIndicator(visibility)
        } else {
            self
        }
    }

    @ViewBuilder
    func compatScrollDismissesKeyboardInteractively() -> some View {
        if #available(iOS 16.0, *) {
            self.scrollDismissesKeyboard(.interactively)
        } else {
            // Retain the standard keyboard behavior of the legacy Form/List.
            self
        }
    }
}
