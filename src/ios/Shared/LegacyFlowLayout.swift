import SwiftUI

/// The legacy flow receives explicit, stable item identities. It never probes
/// SwiftUI's private variadic-view implementation to enumerate opaque children.
struct LegacyFlowItem: Identifiable {
    let id: AnyHashable
    let view: AnyView
    let expectedSize: CGSize

    init<ID: Hashable, Content: View>(id: ID, expectedSize: CGSize = .zero,
                                      @ViewBuilder content: () -> Content) {
        self.id = AnyHashable(id)
        self.expectedSize = expectedSize
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

private struct LegacyFlowWidthKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = nextValue() }
}

struct LegacyFlowLayout: View {
    let items: [LegacyFlowItem]
    var hSpacing: CGFloat = 8
    var vSpacing: CGFloat = 8
    var alignment: HorizontalAlignment = .leading
    /// Explicit content-column width for legacy message attachments. When nil,
    /// retain the measured parent width used by the composer and other callers.
    var widthOverride: CGFloat? = nil
    @State private var sizes: [AnyHashable: CGSize] = [:]
    @State private var availableWidth: CGFloat = 0

    var body: some View {
        // [T-ios15-attachment-right-overflow] A preference can report the
        // parent proposal (for example 428) instead of the trailing content
        // column that actually contains this flow. Do not let that stale
        // number drive positions or alignment. The declared tile sizes are
        // authoritative; measurements only override them once they look like
        // individual tiles rather than the whole parent.
        let itemSizes = items.map { item in
            let measured = sizes[item.id] ?? .zero
            let declared = item.expectedSize
            guard declared.width > 0, declared.height > 0 else { return measured }
            guard measured.width > 0, measured.height > 0,
                  measured.width <= declared.width * 1.5,
                  measured.height <= declared.height * 1.5 else {
                return declared
            }
            return measured
        }
        let contentWidth = items.isEmpty ? CGFloat(0) : itemSizes.reduce(CGFloat(0)) { partial, size in
            max(partial, size.width)
        }
        let measuredWidth = max(availableWidth, 1)
        // Message attachments pass their parent VStack's width explicitly;
        // never replace that with the legacy inner GeometryReader's window-wide
        // proposal. Other callers keep the original measured-width behavior.
        let layoutWidth = max(widthOverride ?? measuredWidth, contentWidth)
        let arrangement = LegacyFlowArrangement.pack(
            sizes: itemSizes, width: layoutWidth, hSpacing: hSpacing,
            vSpacing: vSpacing, trailing: alignment == .trailing)

        ZStack(alignment: .topLeading) {
            // [T-ios15-legacyflow-natural-height] Make the calculated row
            // height part of the ZStack's natural size. The old implementation
            // put the children in a zero-height GeometryReader and then used a
            // preference callback to drive an outer frame. On the legacy
            // renderer the callback could report the right height while that
            // outer frame stayed at zero, leaving 64pt chips visually outside a
            // 6pt attachment view. This spacer makes the parent participate in
            // normal layout, so the measured height is also the allocated
            // height; no height feedback loop is needed.
            Color.clear
                .frame(width: layoutWidth,
                       height: items.isEmpty ? 0 : arrangement.height)

            ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                Group {
                    if itemSizes[index].width > 0, itemSizes[index].height > 0 {
                        item.view
                            .frame(width: itemSizes[index].width, height: itemSizes[index].height)
                            .fixedSize()
                    } else {
                        // Unknown-size callers must keep their intrinsic child
                        // alive for the measurement pass; never force 0×0.
                        item.view.fixedSize()
                    }
                }
                // [T-ios15-legacyflow-measure] overlay, not background:
                // a background GeometryReader sits BEHIND the hosted view
                // and on some legacy layout passes reports the parent's
                // proposal instead of the child's fixedSize ideal size.
                .overlay(
                    GeometryReader { geometry in
                        Color.clear.preference(key: LegacyFlowSizesKey.self,
                                               value: [item.id: geometry.size])
                    }
                )
                .offset(x: arrangement.positions[index].x, y: arrangement.positions[index].y)
            }
        }
        .frame(width: layoutWidth, height: items.isEmpty ? 0 : arrangement.height, alignment: .topLeading)
        .overlay(
            GeometryReader { proxy in
                Color.clear.preference(key: LegacyFlowWidthKey.self, value: proxy.size.width)
            }
        )
        .onPreferenceChange(LegacyFlowSizesKey.self) { measured in
            if sizes != measured { sizes = measured }
        }
        .onPreferenceChange(LegacyFlowWidthKey.self) { measured in
            if abs(availableWidth - measured) > 0.5 { availableWidth = measured }
        }
        // [T-ios15-legacyflow-reset] Discard measurements for removed IDs so
        // the next layout pass cannot place new content using stale sizes.
        .onChange(of: items.map(\.id)) { _ in
            sizes = [:]
        }
    }
}
