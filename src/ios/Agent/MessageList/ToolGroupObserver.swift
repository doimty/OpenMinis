import Foundation
import Combine

/// Observe only status CATEGORIES, not output text or byte counters. A hidden
/// tool's status still updates the visible summary without rebuilding the list.
/// Kept separate from SwiftUI so host tests can run the real Combine observer.
final class ToolGroupObserver: ObservableObject {
    @Published private(set) var summary: ToolGroupSummary
    private var phases: [UUID: ToolGroupPhase]
    private var subscriptions: Set<AnyCancellable> = []

    static func phase(_ status: ToolBlockStatus?) -> ToolGroupPhase {
        switch status {
        case .streaming, .running: return .active
        case .success: return .success
        case .failed: return .failed
        case .cancelled: return .cancelled
        case nil: return .pending
        }
    }

    init(blocks: [AssistantBlock]) {
        phases = Dictionary(blocks.map { ($0.id, Self.phase($0.toolStatus)) },
                            uniquingKeysWith: { first, _ in first })
        summary = ToolGroupSummary(phases: Array(phases.values))
        for block in blocks {
            let id = block.id
            block.$toolStatus
                .map(Self.phase)
                .removeDuplicates()
                .dropFirst()
                .receive(on: DispatchQueue.main)
                .sink { [weak self] phase in
                    guard let self else { return }
                    self.phases[id] = phase
                    let next = ToolGroupSummary(phases: Array(self.phases.values))
                    if next != self.summary { self.summary = next }
                }
                .store(in: &subscriptions)
        }
    }
}
