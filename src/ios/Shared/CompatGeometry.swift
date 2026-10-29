import SwiftUI

private struct LegacyGeometryKey<Element: Equatable>: PreferenceKey {
    static var defaultValue: [UUID: Element] { [:] }
    static func reduce(value: inout [UUID: Element], nextValue: () -> [UUID: Element]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

private struct LegacyGeometryObserver<Element: Equatable>: ViewModifier {
    let transform: (GeometryProxy) -> Element
    let action: (Element) -> Void
    @State private var identity = UUID()
    @State private var previous: Element?

    func body(content: Content) -> some View {
        content
            .background(GeometryReader { proxy in
                Color.clear.preference(key: LegacyGeometryKey<Element>.self,
                                       value: [identity: transform(proxy)])
            })
            .onPreferenceChange(LegacyGeometryKey<Element>.self) { values in
                guard let value = values[identity], previous != value else { return }
                previous = value
                action(value)
            }
    }
}

extension View {
    @ViewBuilder
    func compatOnGeometryChange<Element: Equatable>(
        for type: Element.Type, of transform: @escaping (GeometryProxy) -> Element,
        action: @escaping (Element) -> Void
    ) -> some View {
        if #available(iOS 16.0, *) {
            onGeometryChange(for: type, of: transform, action: action)
        } else {
            modifier(LegacyGeometryObserver(transform: transform, action: action))
        }
    }
}
