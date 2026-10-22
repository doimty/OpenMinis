// Foundation-only projection shared by the live V3 list and host regressions.
import Foundation

/// Each original block keeps its identity even while it is hidden by a group.
enum MessageListItem: Hashable {
    case wholeMessage(UUID)
    case assistantHeader(UUID)
    case assistantBlock(UUID, UUID)
    case assistantToolGroup(ToolGroupID)
    case assistantFooter(UUID)

    /// Representative owner for turn navigation. A group can have MORE owners;
    /// search, screenshots and live-height exclusion must use ownerMessageIDs.
    var messageId: UUID {
        switch self {
        case .wholeMessage(let id), .assistantHeader(let id), .assistantFooter(let id): return id
        case .assistantBlock(let mid, _): return mid
        case .assistantToolGroup(let id): return id.messageID
        }
    }

    func ownerMessageIDs(groups: [ToolGroupID: ToolGroupDescriptor]) -> Set<UUID> {
        if case .assistantToolGroup(let id) = self, let group = groups[id] {
            return Set(group.members.map(\.messageID))
        }
        return [messageId]
    }
}

enum ToolGroupMetrics {
    /// Fixed single-line summary height. Shared by the SwiftUI row and the
    /// list's height estimator so the seed can never disagree with the render.
    static let rowHeight: CGFloat = 44
}

/// Only the FIRST member determines diffable identity. Appending another tool
/// changes the descriptor, not the identity (and not the user's expansion choice).
struct ToolGroupID: Hashable {
    let messageID: UUID
    let blockID: UUID
}

struct ToolGroupMember: Hashable {
    let messageID: UUID
    let blockID: UUID
    var item: MessageListItem { .assistantBlock(messageID, blockID) }
}

struct ToolGroupDescriptor: Hashable {
    let id: ToolGroupID
    let members: [ToolGroupMember]
}

struct ToolGroupProjection {
    let items: [MessageListItem]
    let groups: [ToolGroupID: ToolGroupDescriptor]

    /// `toolItems` is built using an explicit AssistantBlockKind switch by the
    /// caller, NOT toolStatus: a missing status must never swallow text/thinking.
    /// Every non-tool item is a hard boundary, including empty footers. Scanning
    /// the ORIGINAL ordered stream preserves callbacks, errors and resume rows.
    static func build(items: [MessageListItem], toolItems: Set<MessageListItem>,
                      expanded: Set<ToolGroupID>) -> ToolGroupProjection {
        var result: [MessageListItem] = []
        var groups: [ToolGroupID: ToolGroupDescriptor] = [:]
        var run: [ToolGroupMember] = []
        func flush() {
            guard let first = run.first else { return }
            if run.count >= 2 {
                let id = ToolGroupID(messageID: first.messageID, blockID: first.blockID)
                groups[id] = ToolGroupDescriptor(id: id, members: run)
                result.append(.assistantToolGroup(id))
                if expanded.contains(id) { result.append(contentsOf: run.map(\.item)) }
            } else {
                result.append(first.item)
            }
            run.removeAll(keepingCapacity: true)
        }
        for item in items {
            if case .assistantBlock(let mid, let bid) = item, toolItems.contains(item) {
                run.append(ToolGroupMember(messageID: mid, blockID: bid))
            } else {
                flush()
                result.append(item)
            }
        }
        flush()
        return ToolGroupProjection(items: result, groups: groups)
    }

    /// A stable item list alone cannot detect tools appended to a collapsed
    /// group. These survivors MUST be reconfigured before the fast skip path.
    func changedGroups(comparedTo previous: [ToolGroupID: ToolGroupDescriptor],
                       expanded: Set<ToolGroupID>, previouslyExpanded: Set<ToolGroupID>) -> Set<ToolGroupID> {
        Set(groups.keys.filter { id in
            previous[id] != nil && (previous[id] != groups[id]
                || expanded.contains(id) != previouslyExpanded.contains(id))
        })
    }
}

/// Status categories deliberately ignore streaming byte counts: the summary
/// doesn't grow every token and cannot be frozen by a stale text-height memo.
enum ToolGroupPhase: Equatable {
    case pending, active, success, failed, cancelled
}

struct ToolGroupSummary: Equatable {
    let total: Int
    let active: Int
    let pending: Int
    let failed: Int
    let cancelled: Int
    let succeeded: Int

    init(phases: [ToolGroupPhase]) {
        total = phases.count
        active = phases.filter { $0 == .active }.count
        pending = phases.filter { $0 == .pending }.count
        failed = phases.filter { $0 == .failed }.count
        cancelled = phases.filter { $0 == .cancelled }.count
        succeeded = phases.filter { $0 == .success }.count
    }
    var isActive: Bool { active > 0 }
    var allSucceeded: Bool { total > 0 && succeeded == total }
}
