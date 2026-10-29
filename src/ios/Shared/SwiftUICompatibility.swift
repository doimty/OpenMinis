import SwiftUI
import UniformTypeIdentifiers

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

/// A vertical editor must remain multiline on iOS 15. A lineLimit modifier
/// cannot turn the legacy single-line TextField into a multiline editor.
struct CompatMultilineTextField: View {
    @Binding private var text: String
    private let label: Text
    private let lineLimit: ClosedRange<Int>?
    @ScaledMetric(relativeTo: .body) private var lineHeight: CGFloat = 20

    init(_ title: LocalizedStringKey, text: Binding<String>, lineLimit: ClosedRange<Int>? = nil) {
        self._text = text
        self.label = Text(title)
        self.lineLimit = lineLimit
    }

    init<S: StringProtocol>(_ title: S, text: Binding<String>, lineLimit: ClosedRange<Int>? = nil) {
        self._text = text
        self.label = Text(verbatim: String(title))
        self.lineLimit = lineLimit
    }

    @ViewBuilder
    var body: some View {
        if #available(iOS 16.0, *) {
            if let lineLimit {
                TextField(text: $text, axis: .vertical) { label }
                    .lineLimit(lineLimit)
            } else {
                TextField(text: $text, axis: .vertical) { label }
            }
        } else if let lineLimit {
            legacyEditor
                .frame(minHeight: lineHeight * CGFloat(lineLimit.lowerBound) + 16,
                       maxHeight: lineHeight * CGFloat(lineLimit.upperBound) + 16)
        } else {
            // The transcript's existing bounded parent owns the height and
            // scrolling. Never add an outer ScrollView around this editor.
            legacyEditor.frame(minHeight: lineHeight + 16)
        }
    }

    private var legacyEditor: some View {
        TextEditor(text: $text)
            .overlay(alignment: .topLeading) {
                if text.isEmpty {
                    label
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 5)
                        .padding(.vertical, 8)
                        .allowsHitTesting(false)
                        .accessibilityHidden(true)
                }
            }
            .accessibilityLabel(label)
    }
}

/// Local path erasure avoids exposing iOS-16-only AnyShape in stored types.
struct CompatAnyShape: Shape {
    private let makePath: (CGRect) -> Path
    init<S: Shape>(_ shape: S) { makePath = { shape.path(in: $0) } }
    func path(in rect: CGRect) -> Path { makePath(rect) }
}

/// Keep square joining edges on grouped rows. Rounding every corner is not an
/// equivalent fallback. Native platforms retain their continuous corner path.
struct CompatUnevenRoundedRectangle: Shape {
    var topLeadingRadius: CGFloat = 0
    var bottomLeadingRadius: CGFloat = 0
    var bottomTrailingRadius: CGFloat = 0
    var topTrailingRadius: CGFloat = 0
    var style: RoundedCornerStyle = .continuous

    func path(in rect: CGRect) -> Path {
        if #available(iOS 16.0, *) {
            return UnevenRoundedRectangle(
                topLeadingRadius: topLeadingRadius, bottomLeadingRadius: bottomLeadingRadius,
                bottomTrailingRadius: bottomTrailingRadius, topTrailingRadius: topTrailingRadius,
                style: style).path(in: rect)
        }
        let limit = max(0, min(rect.width, rect.height) / 2)
        let tl = min(limit, max(0, topLeadingRadius))
        let tr = min(limit, max(0, topTrailingRadius))
        let bl = min(limit, max(0, bottomLeadingRadius))
        let br = min(limit, max(0, bottomTrailingRadius))
        var path = Path()
        path.move(to: CGPoint(x: rect.minX + tl, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX - tr, y: rect.minY))
        path.addQuadCurve(to: CGPoint(x: rect.maxX, y: rect.minY + tr),
                          control: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - br))
        path.addQuadCurve(to: CGPoint(x: rect.maxX - br, y: rect.maxY),
                          control: CGPoint(x: rect.maxX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX + bl, y: rect.maxY))
        path.addQuadCurve(to: CGPoint(x: rect.minX, y: rect.maxY - bl),
                          control: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + tl))
        path.addQuadCurve(to: CGPoint(x: rect.minX + tl, y: rect.minY),
                          control: CGPoint(x: rect.minX, y: rect.minY))
        path.closeSubpath()
        return path
    }
}

extension ToolbarItemPlacement {
    static var compatSecondaryAction: ToolbarItemPlacement {
        if #available(iOS 16.0, *) { return .secondaryAction }
        return .navigationBarTrailing
    }
}

extension Shape {
    @ViewBuilder
    func compatFillGradient(_ color: Color) -> some View {
        if #available(iOS 16.0, *) {
            fill(color.gradient)
        } else {
            fill(LinearGradient(colors: [color.opacity(0.8), color],
                                startPoint: .top, endPoint: .bottom))
        }
    }
}

extension View {
    /// iOS 16+ retains native draggable; older systems use the same id payload
    /// with the legacy onDrag API. The context-menu/long-press arbitration is
    /// owned by the system on both paths.
    @ViewBuilder
    func compatDraggable(_ payload: String) -> some View {
        if #available(iOS 16.0, *) {
            draggable(payload)
        } else {
            onDrag {
                NSItemProvider(object: payload as NSString)
            }
        }
    }

    /// iOS 16+ retains native dropDestination. Older systems use onDrop with a
    /// String payload; every readable provider contributes one value, failed
    /// providers are ignored, and a nonempty batch is delivered to the caller's
    /// existing main-actor action. The action's Bool return cannot be reflected
    /// synchronously on the legacy path.
    @ViewBuilder
    func compatDropDestination(
        for payloadType: String.Type,
        action: @escaping ([String], CGPoint) -> Bool,
        isTargeted: @escaping (Bool) -> Void
    ) -> some View {
        if #available(iOS 16.0, *) {
            dropDestination(for: payloadType, action: action, isTargeted: isTargeted)
        } else {
            LegacyStringDropTarget(content: self, action: { values in action(values, .zero) }, isTargeted: isTargeted)
        }
    }

    /// iOS 16+ retains custom split-column width; the legacy NavigationView
    /// uses system layout constraints.
    @ViewBuilder
    func compatNavigationSplitViewColumnWidth(min: CGFloat, ideal: CGFloat, max: CGFloat) -> some View {
        if #available(iOS 16.0, *) {
            navigationSplitViewColumnWidth(min: min, ideal: ideal, max: max)
        } else {
            self
        }
    }

    /// iOS 16+ pins the row separator to the row's leading edge so centred
    /// hint rows still draw a full-width divider. The legacy List has no
    /// equivalent guide, so the separator follows the content's own leading
    /// edge on older systems.
    @ViewBuilder
    func compatListRowSeparatorLeading() -> some View {
        if #available(iOS 16.0, *) {
            alignmentGuide(.listRowSeparatorLeading) { _ in 0 }
        } else {
            self
        }
    }
}

/// onDrop's isTargeted is a Binding, not a callback. A tiny owning view
/// provides the legacy state and forwards changes to the caller's closure.
private struct LegacyStringDropTarget<Content: View>: View {
    let content: Content
    let action: ([String]) -> Bool
    let isTargeted: (Bool) -> Void
    @State private var targeted = false

    var body: some View {
        content
            .onDrop(of: [UTType.text.identifier], isTargeted: $targeted) { providers in
                guard !providers.isEmpty else { return false }
                let accepted = LegacyStringDrop.readableProviders(from: providers)
                guard !accepted.isEmpty else { return false }
                Task { @MainActor in
                    let values = await LegacyStringDrop.loadValues(from: accepted)
                    guard !values.isEmpty, !Task.isCancelled else { return }
                    _ = action(values)
                }
                return true
            }
            .onChange(of: targeted) { newValue in isTargeted(newValue) }
    }
}

private struct LegacyFontWeight: ViewModifier {
    @Environment(\.font) private var inheritedFont
    let weight: Font.Weight
    func body(content: Content) -> some View {
        content.font((inheritedFont ?? .body).weight(weight))
    }
}

extension View {
    /// Unlike Text.fontWeight, the general View modifier requires iOS 16.
    @ViewBuilder
    func compatFontWeight(_ weight: Font.Weight) -> some View {
        if #available(iOS 16.0, *) { fontWeight(weight) }
        else { modifier(LegacyFontWeight(weight: weight)) }
    }

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
    func compatNavigationBarHidden(_ hidden: Bool) -> some View {
        if #available(iOS 16.0, *) { toolbar(hidden ? .hidden : .visible, for: .navigationBar) }
        else { navigationBarHidden(hidden) }
    }

    @ViewBuilder
    func compatPersistentSystemOverlays(_ visibility: Visibility) -> some View {
        if #available(iOS 16.0, *) { persistentSystemOverlays(visibility) }
        else { self } // iOS 15 keeps its system home indicator and exit gestures.
    }

    @ViewBuilder
    func compatContextMenu<MenuItems: View, Preview: View>(
        @ViewBuilder menuItems: @escaping () -> MenuItems,
        @ViewBuilder preview: @escaping () -> Preview
    ) -> some View {
        if #available(iOS 16.0, *) {
            contextMenu(menuItems: menuItems, preview: preview)
        } else {
            // Keep every menu action. Only the optional custom preview is lost.
            contextMenu(menuItems: menuItems)
        }
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
