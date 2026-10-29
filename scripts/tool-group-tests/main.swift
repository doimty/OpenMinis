import Foundation

// Native host regression for the production ToolGroupProjection.
// Expectations are written per case (not by re-running the same algorithm).

var failures = 0
var checks = 0
func expect(_ condition: Bool, _ message: String) {
    checks += 1
    if !condition {
        failures += 1
        FileHandle.standardError.write(("FAIL: " + message + "\n").data(using: .utf8)!)
    }
}

let m1 = UUID(), m2 = UUID(), m3 = UUID()
let t1 = UUID(), t2 = UUID(), t3 = UUID(), t4 = UUID(), t5 = UUID()

func block(_ m: UUID, _ b: UUID) -> MessageListItem { .assistantBlock(m, b) }
func header(_ m: UUID) -> MessageListItem { .assistantHeader(m) }
func footer(_ m: UUID) -> MessageListItem { .assistantFooter(m) }
func user(_ m: UUID) -> MessageListItem { .wholeMessage(m) }

func groupID(_ p: ToolGroupProjection) -> ToolGroupID? { p.groups.keys.first }
func rows(_ p: ToolGroupProjection) -> [MessageListItem] { p.items }

// MARK: - 1. Threshold

do {
    let none = ToolGroupProjection.build(items: [header(m1), footer(m1)], toolItems: [], expanded: [])
    expect(rows(none) == [header(m1), footer(m1)], "no tools leaves the stream untouched")
    expect(none.groups.isEmpty, "no tools creates no descriptor")

    let single = ToolGroupProjection.build(items: [header(m1), block(m1, t1), footer(m1)],
                                           toolItems: [block(m1, t1)], expanded: [])
    expect(rows(single) == [header(m1), block(m1, t1), footer(m1)], "one tool stays a normal row")
    expect(single.groups.isEmpty, "one tool never collapses")
}

// MARK: - 2. Two adjacent tools fold, anchored by the first block

do {
    let seq = [header(m1), block(m1, t1), block(m1, t2), footer(m1)]
    let p = ToolGroupProjection.build(items: seq, toolItems: [block(m1, t1), block(m1, t2)], expanded: [])
    expect(rows(p).count == 3, "pair collapses to header + summary + footer")
    guard let gid = groupID(p), case .assistantToolGroup(let visible) = rows(p)[1] else {
        fatalError("expected a visible summary row")
    }
    expect(visible == gid, "summary carries the descriptor identity")
    expect(gid.blockID == t1 && gid.messageID == m1, "identity is the FIRST block")
    expect(p.groups[gid]?.members.map(\.item) == [block(m1, t1), block(m1, t2)],
           "descriptor keeps members in original order")
}

// MARK: - 3. Every non-tool row is a hard boundary

do {
    let boundaries: [(String, MessageListItem)] = [
        ("text", block(m1, t3)), ("header", header(m2)), ("footer", footer(m2)),
        ("user", user(m3)), ("system", user(m2)),
    ]
    // t3 is deliberately NOT in toolItems: text/thinking/info must break runs.
    for (name, mid) in boundaries {
        let seq = [block(m1, t1), mid, block(m1, t2)]
        let p = ToolGroupProjection.build(items: seq, toolItems: [block(m1, t1), block(m1, t2)], expanded: [])
        expect(p.groups.isEmpty, "\(name) prevents folding across it")
        expect(rows(p) == seq, "\(name) keeps original order with both tools visible")
    }
}

// MARK: - 4. Adjacent assistant messages may share one group

do {
    let seq = [header(m1), block(m1, t1), block(m1, t2), block(m2, t3)]
    let tools: Set<MessageListItem> = [block(m1, t1), block(m1, t2), block(m2, t3)]
    let p = ToolGroupProjection.build(items: seq, toolItems: tools, expanded: [])
    guard let gid = groupID(p), let d = p.groups[gid] else { fatalError("missing cross-message group") }
    expect(d.members.count == 3, "all three adjacent tools share one group")
    expect(rows(p).count == 2, "header + one summary")
    let item = MessageListItem.assistantToolGroup(gid)
    expect(item.ownerMessageIDs(groups: p.groups) == [m1, m2], "summary owns both messages")
    expect(MessageListItem.assistantBlock(m2, t3).ownerMessageIDs(groups: p.groups) == [m2],
           "a plain block still owns exactly its message")
}

// MARK: - 5. Expansion restores the original rows in place

do {
    let seq = [header(m1), block(m1, t1), block(m1, t2), footer(m1)]
    let tools: Set<MessageListItem> = [block(m1, t1), block(m1, t2)]
    let p = ToolGroupProjection.build(items: seq, toolItems: tools, expanded: [])
    guard let gid = groupID(p) else { fatalError("missing group") }
    let x = ToolGroupProjection.build(items: seq, toolItems: tools, expanded: [gid])
    expect(rows(x) == [header(m1), .assistantToolGroup(gid), block(m1, t1), block(m1, t2), footer(m1)],
           "expanded order is summary then each original block, boundaries intact")
    expect(Set(rows(x)).count == rows(x).count, "expanded rows contain no duplicate identifiers")
    expect(x.groups[gid]?.members.count == 2, "expansion does not change membership")
}

// MARK: - 6..8. Same identity, changed descriptor/expansion must be reported

do {
    let a = ToolGroupProjection.build(items: [block(m1, t1), block(m1, t2)],
                                      toolItems: [block(m1, t1), block(m1, t2)], expanded: [])
    let b = ToolGroupProjection.build(items: [block(m1, t1), block(m1, t2), block(m2, t3)],
                                      toolItems: [block(m1, t1), block(m1, t2), block(m2, t3)], expanded: [])
    guard let gid = groupID(a), let gidB = groupID(b) else { fatalError("missing groups") }
    expect(gid == gidB, "appending a tool keeps the same diffable identity")
    expect(a.items == b.items, "the visible item list is unchanged by a hidden third tool")
    let changed = b.changedGroups(comparedTo: a.groups, expanded: [], previouslyExpanded: [])
    expect(changed == [gid], "a descriptor change is reported so the summary reconfigures")

    let stable = b.changedGroups(comparedTo: b.groups, expanded: [], previouslyExpanded: [])
    expect(stable.isEmpty, "an identical pass reports nothing")

    let expanded = b.changedGroups(comparedTo: b.groups, expanded: [gid], previouslyExpanded: [])
    expect(expanded == [gid], "an expansion-only change still reconfigures")
}

// MARK: - 9. Losing a member degrades back to a normal row

do {
    let two = ToolGroupProjection.build(items: [block(m1, t1), block(m1, t2)],
                                        toolItems: [block(m1, t1), block(m1, t2)], expanded: [])
    guard let old = groupID(two) else { fatalError("missing group") }
    let one = ToolGroupProjection.build(items: [block(m1, t1)], toolItems: [block(m1, t1)], expanded: [old])
    expect(one.groups.isEmpty, "a single remaining tool stops being a group")
    expect(rows(one) == [block(m1, t1)], "the survivor renders as its own row")
    expect(one.changedGroups(comparedTo: two.groups, expanded: [], previouslyExpanded: [old]).isEmpty,
           "a removed group is not reconfigured (it is an insert/delete)")
}

// MARK: - 10. Summary arithmetic

do {
    expect(ToolGroupSummary(phases: []).total == 0, "empty summary has no total")
    expect(!ToolGroupSummary(phases: []).allSucceeded, "empty is not 'all succeeded'")

    let mixed = ToolGroupSummary(phases: [.active, .success, .failed])
    expect(mixed.total == 3 && mixed.active == 1 && mixed.succeeded == 1 && mixed.failed == 1,
           "mixed phases are counted per category")
    expect(!mixed.allSucceeded, "a failure blocks the all-succeeded state")
    expect(mixed.isActive, "an active member is visible")

    let done = ToolGroupSummary(phases: [.success, .success])
    expect(done.allSucceeded && !done.isActive && done.failed == 0, "all success is reported as success")

    let cancelled = ToolGroupSummary(phases: [.cancelled, .success])
    expect(cancelled.cancelled == 1 && !cancelled.allSucceeded, "cancellation is not success")
}

// MARK: - 11. Deterministic round-trip over a longer mixed stream

do {
    let seq: [MessageListItem] = [
        header(m1), block(m1, t1), block(m1, t2), block(m1, t3),
        block(m2, t4), footer(m1),                 // footer closes the run after t4
        header(m3), block(m3, t5), footer(m3),
    ]
    let tools: Set<MessageListItem> = [block(m1, t1), block(m1, t2), block(m1, t3), block(m2, t4), block(m3, t5)]
    let p = ToolGroupProjection.build(items: seq, toolItems: tools, expanded: [])
    // Two-run expectation, written out rather than recomputed:
    // [header, summary(m1/t1..m2/t4 -> 4 members), footer, header, tool, footer]
    expect(rows(p).count == 6, "long stream folds into two summaries and the single tool")
    expect(p.groups.count == 1, "only the 4-tool run forms a group")
    if let gid = groupID(p) {
        expect(p.groups[gid]?.members.map(\.blockID) == [t1, t2, t3, t4],
               "group members preserve exact order across the message boundary")
        let x = ToolGroupProjection.build(items: seq, toolItems: tools, expanded: [gid])
        let rebuilt = rows(x).compactMap { item -> MessageListItem? in
            if case .assistantToolGroup = item { return nil }
            return item
        }
        expect(rebuilt == seq, "expanding everything reproduces the original stream exactly")
    } else {
        expect(false, "expected exactly one group in the long stream")
    }
}

// MARK: - Result

if failures > 0 {
    FileHandle.standardError.write("FAILED \(failures)/\(checks) assertions\n".data(using: .utf8)!)
    exit(1)
}
print("PASS: \(checks) native tool-group assertions")
