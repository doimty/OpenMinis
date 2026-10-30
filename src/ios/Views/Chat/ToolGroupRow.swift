import SwiftUI

enum ToolGroupMetrics {
    /// Fixed single-line summary height. Shared by the SwiftUI row and the
    /// list's height estimator so the seed can never disagree with the render.
    static let rowHeight: CGFloat = 44
}

/// Exact identity, not a hash-only surrogate: replacing message objects under
/// unchanged block IDs must replace the StateObject's Combine subscriptions.
struct ToolGroupObserverIdentity: Hashable {
    let members: [ObjectIdentifier]
    let expanded: Bool
}

/// One small summary host. Expanded children remain separate V3 cells, never
/// an unbounded VStack of tools inside one self-sizing UICollectionView cell.
struct ToolGroupRow: View {
    @StateObject private var observer: ToolGroupObserver
    @EnvironmentObject private var vm: AIChatViewModel
    let isExpanded: Bool
    let onToggle: () -> Void
    let onStop: () -> Void

    init(blocks: [AssistantBlock], isExpanded: Bool,
         onToggle: @escaping () -> Void, onStop: @escaping () -> Void) {
        _observer = StateObject(wrappedValue: ToolGroupObserver(blocks: blocks))
        self.isExpanded = isExpanded
        self.onToggle = onToggle
        self.onStop = onStop
    }

    private var statusText: String {
        let s = observer.summary
        var parts: [String] = []
        if s.active > 0 { parts.append(String(format: AppLocalized("%lld running"), s.active)) }
        if s.failed > 0 { parts.append(String(format: AppLocalized("%lld failed"), s.failed)) }
        if s.cancelled > 0 { parts.append(String(format: AppLocalized("%lld cancelled"), s.cancelled)) }
        if s.pending > 0 { parts.append(String(format: AppLocalized("%lld pending"), s.pending)) }
        return parts.isEmpty ? AppLocalized("Completed") : parts.joined(separator: " · ")
    }

    private var statusColor: Color {
        if observer.summary.failed > 0 { return .red }
        if observer.summary.isActive { return .accentColor }
        if observer.summary.cancelled > 0 { return .orange }
        return .secondary
    }

    var body: some View {
        HStack(spacing: 0) {
            Button(action: onToggle) {
                HStack(spacing: 8) {
                    Image(systemName: "chevron.right")
                        .rotationEffect(.degrees(isExpanded ? 90 : 0))
                        .font(.system(size: 10, weight: .semibold))
                    Image(systemName: observer.summary.failed > 0
                          ? "exclamationmark.triangle" : "square.stack.3d.up")
                        .foregroundColor(statusColor)
                    Text(String(format: AppLocalized("%lld tool calls"), observer.summary.total))
                        .font(.system(size: 13, weight: .medium))
                        .lineLimit(1)
                        .layoutPriority(1)
                    Spacer(minLength: 4)
                    if observer.summary.isActive {
                        ProgressView().scaleEffect(0.7).frame(width: 16)
                    }
                    Text(statusText)
                        .font(.system(size: 11))
                        .foregroundColor(statusColor)
                        .lineLimit(1)
                }
                .padding(.horizontal, 10)
                .frame(maxWidth: .infinity, minHeight: ToolGroupMetrics.rowHeight)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel(String(format: AppLocalized("%lld tool calls"), observer.summary.total)
                                + ", " + statusText)
            .accessibilityHint(isExpanded ? AppLocalized("Collapse tool calls") : AppLocalized("Expand tool calls"))
            .accessibilityIdentifier("assistantToolGroupToggle")

            // Same global stop callback as the original active tool cards. A
            // nested button would steal the expand tap, so this is a sibling.
            if observer.summary.isActive, vm.isProcessing {
                Button(action: onStop) {
                    Image(systemName: "stop.circle")
                        .font(.system(size: 17))
                        .frame(width: 44, height: ToolGroupMetrics.rowHeight)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .foregroundColor(.red)
                .accessibilityLabel(AppLocalized("Stop"))
                .accessibilityIdentifier("assistantToolGroupStop")
            }
        }
        .frame(height: ToolGroupMetrics.rowHeight)
        .background(ChatColors.primaryText.opacity(0.045))
        .clipShape(RoundedRectangle(cornerRadius: 10))
        .accessibilityIdentifier("assistantToolGroup")
    }
}
