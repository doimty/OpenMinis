import SwiftUI

/// The legacy flow receives explicit, stable item identities. It never probes
/// SwiftUI's private variadic-view implementation to enumerate opaque children.
struct LegacyFlowItem: Identifiable {
    let id: AnyHashable
    let view: AnyView

    init<ID: Hashable, Content: View>(id: ID, @ViewBuilder content: () -> Content) {
        self.id = AnyHashable(id)
        self.view = AnyView(content())
    }
}

struct LegacyFlowArrangement {
    let positions: [CGPoint]
    let height: CGFloat

    /// Mirrors FlowLayout's row packing, including trailing alignment of a
    /// partially filled last row and the largest item height in each row.
    static func pack(sizes: [CGSize], width: CGFloat, hSpacing: CGFloat,
                     vSpacing: CGFloat, trailing: Bool) -> LegacyFlowArrangement {
        var positions: [CGPoint] = []
        var rowStart = 0
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        func alignRow(endingAt end: Int) {
            guard trailing, end > rowStart else { return }
            let offset = width - (x - hSpacing)
            for index in rowStart..<end { positions[index].x += offset }
        }
        for (index, size) in sizes.enumerated() {
            if x + size.width > width, x > 0 {
                alignRow(endingAt: index)
                rowStart = index
                x = 0
                y += rowHeight + vSpacing
                rowHeight = 0
            }
            positions.append(CGPoint(x: x, y: y))
            rowHeight = max(rowHeight, size.height)
            x += size.width + hSpacing
        }
        alignRow(endingAt: sizes.count)
        return LegacyFlowArrangement(positions: positions, height: sizes.isEmpty ? 0 : y + rowHeight)
    }
}

private struct LegacyFlowSizesKey: PreferenceKey {
    static var defaultValue: [AnyHashable: CGSize] = [:]
    static func reduce(value: inout [AnyHashable: CGSize], nextValue: () -> [AnyHashable: CGSize]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

private struct LegacyFlowHeightKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

struct LegacyFlowLayout: View {
    let items: [LegacyFlowItem]
    var hSpacing: CGFloat = 8
    var vSpacing: CGFloat = 8
    var alignment: HorizontalAlignment = .leading
    @State private var sizes: [AnyHashable: CGSize] = [:]
    @State private var height: CGFloat = 1

    var body: some View {
        GeometryReader { proxy in
            let itemSizes = items.map { sizes[$0.id] ?? .zero }
            let arrangement = LegacyFlowArrangement.pack(
                sizes: itemSizes, width: proxy.size.width, hSpacing: hSpacing,
                vSpacing: vSpacing, trailing: alignment == .trailing)
            ZStack(alignment: .topLeading) {
                ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                    item.view
                        .fixedSize()
                        .background(GeometryReader { geometry in
                            Color.clear.preference(key: LegacyFlowSizesKey.self,
                                                   value: [item.id: geometry.size])
                        })
                        .offset(x: arrangement.positions[index].x, y: arrangement.positions[index].y)
                }
            }
            .frame(width: proxy.size.width, height: arrangement.height, alignment: .topLeading)
            .preference(key: LegacyFlowHeightKey.self, value: arrangement.height)
        }
        .frame(height: items.isEmpty ? 0 : height)
        .onPreferenceChange(LegacyFlowSizesKey.self) { measured in
            if sizes != measured { sizes = measured }
        }
        .onPreferenceChange(LegacyFlowHeightKey.self) { measured in
            if abs(height - measured) > 0.5 { height = measured }
        }
    }
}
