import Foundation
import CoreGraphics

struct CheckFailure: Error, CustomStringConvertible {
    let description: String
}
func require(_ value: @autoclosure () -> Bool, _ message: String) throws {
    if !value() { throw CheckFailure(description: message) }
}

func makeLayout() -> MessageListLayout {
    let layout = MessageListLayout()
    layout.collectionView = UICollectionView()
    layout.itemSpacing = 0
    return layout
}

func attributes(_ height: CGFloat, row: Int = 0) -> UICollectionViewLayoutAttributes {
    let result = UICollectionViewLayoutAttributes(forCellWith: IndexPath(item: row, section: 0))
    result.frame = CGRect(x: 0, y: 0, width: 428, height: height)
    return result
}

// Invoke the same public admission -> invalidation path used by UIKit. We do
// not implement its scheduler or measurement. Preferred values are test inputs.
@discardableResult
func observe(_ layout: MessageListLayout, _ height: CGFloat, row: Int = 0) -> Bool {
    layout.prepare()
    let original = layout.layoutAttributesForItem(at: IndexPath(item: row, section: 0))!
    let preferred = attributes(height, row: row)
    let admitted = layout.shouldInvalidateLayout(forPreferredLayoutAttributes: preferred,
                                                 withOriginalAttributes: original)
    if admitted {
        _ = layout.invalidationContext(forPreferredLayoutAttributes: preferred,
                                       withOriginalAttributes: original)
        layout.prepare()
    }
    return admitted
}

func height(_ layout: MessageListLayout, row: Int = 0) -> CGFloat {
    layout.prepare()
    return layout.layoutAttributesForItem(at: IndexPath(item: row, section: 0))!.size.height
}

func pendingLayout() -> MessageListLayout {
    let layout = makeLayout()
    observe(layout, 1631)
    layout.deferSelfSizing = true
    observe(layout, 1376)
    layout.deferSelfSizing = false
    return layout
}

func changeWidth(_ layout: MessageListLayout, to width: CGFloat) {
    let bounds = CGRect(x: 0, y: 0, width: width, height: 801)
    _ = layout.shouldInvalidateLayout(forBoundsChange: bounds)
    layout.collectionView!.bounds = bounds
}

func drainWidthCallback() {
    RunLoop.main.run(until: Date().addingTimeInterval(0.12))
}

@main
struct PolicyTests {
    static func main() {
        let tests: [(String, () throws -> Void)] = [
            ("precalc_shrink_is_deferred", {
                let layout = makeLayout()
                layout.setPrecalcHeight(1631, at: 0)
                layout.deferSelfSizing = true
                try require(!observe(layout, 1376), "precalc-only row admitted the captured provisional shrink")
                try require(height(layout) == 1631, "provisional height changed the real layout attributes")
                try require(layout.cachedHeight(at: 0) == nil, "precalculation was promoted into measured cache")
                try require(layout.deferredHeightCount == 1, "legitimate pending shrink was discarded")
            }),
            ("unknown_first_size_is_admitted", {
                let layout = makeLayout()
                layout.setEstimatedHeight(200, at: 0)
                layout.deferSelfSizing = true
                try require(observe(layout, 147), "coarse estimate incorrectly became a protected measurement")
                try require(height(layout) == 147, "first real size did not update layout")
            }),
            ("precalc_growth_is_admitted", {
                let layout = makeLayout()
                layout.setPrecalcHeight(1631, at: 0)
                layout.deferSelfSizing = true
                try require(observe(layout, 1865), "valid growth was clipped by deferred sizing")
                try require(height(layout) == 1865, "growth did not reach actual layout attributes")
            }),
            ("idle_precalc_shrink_is_admitted", {
                let layout = makeLayout()
                layout.setPrecalcHeight(1631, at: 0)
                try require(observe(layout, 1376), "nondeferred legitimate shrink stopped working")
                try require(height(layout) == 1376, "idle shrink was not committed")
            }),
            ("stable_cancels_pending_shrink", {
                let layout = makeLayout()
                observe(layout, 1631)
                layout.deferSelfSizing = true
                observe(layout, 1376)
                try require(layout.deferredHeightCount == 1, "test did not queue its shrink")
                observe(layout, 1631)
                layout.deferSelfSizing = false
                layout.applyDeferredHeights()
                try require(height(layout) == 1631, "restored height was overwritten by obsolete pending shrink")
            }),
            ("post_thaw_stable_cancels_pending_shrink", {
                let layout = makeLayout()
                observe(layout, 1631)
                layout.deferSelfSizing = true
                observe(layout, 1376)
                layout.deferSelfSizing = false
                observe(layout, 1631)
                layout.applyDeferredHeights()
                try require(height(layout) == 1631, "fresh correction after thaw did not cancel old pending value")
            }),
            ("growth_cancels_pending_shrink", {
                let layout = makeLayout()
                observe(layout, 1631)
                layout.deferSelfSizing = true
                observe(layout, 1376)
                try require(observe(layout, 1865), "valid growth was rejected")
                layout.deferSelfSizing = false
                layout.applyDeferredHeights()
                try require(height(layout) == 1865, "accepted growth was undone by an older pending shrink")
            }),
            ("deadband_growth_cancels_pending_shrink", {
                let layout = makeLayout()
                observe(layout, 1000)
                layout.deferSelfSizing = true
                observe(layout, 900)
                try require(!observe(layout, 1001), "existing two-point measured-cache deadband changed")
                layout.deferSelfSizing = false
                layout.applyDeferredHeights()
                try require(height(layout) == 1000, "deadband observation failed to supersede obsolete shrink")
            }),
            ("reset_drops_pending", {
                let layout = pendingLayout()
                layout.clearHeightCache()
                layout.applyDeferredHeights()
                try require(layout.cachedHeight(at: 0) == nil, "old session height was resurrected after reset")
            }),
            ("explicit_invalidation_drops_pending", {
                let layout = pendingLayout()
                layout.invalidateHeight(at: 0)
                layout.applyDeferredHeights()
                try require(layout.cachedHeight(at: 0) == nil, "explicitly invalidated content regained its old pending height")
            }),
            ("accepted_write_drops_pending", {
                let layout = pendingLayout()
                _ = layout.invalidationContext(forPreferredLayoutAttributes: attributes(1865),
                                               withOriginalAttributes: attributes(1631))
                layout.applyDeferredHeights()
                try require(height(layout) == 1865, "direct accepted cache write was undone by pending shrink")
            }),
            ("authoritative_write_drops_pending", {
                let layout = pendingLayout()
                layout.setCachedHeight(1800, at: 0)
                layout.applyDeferredHeights()
                try require(height(layout) == 1800, "authoritative GeometryReader height was overwritten")
            }),
            ("confirmed_height_rejects_pending", {
                let layout = makeLayout()
                layout.setCachedHeight(1631, at: 0)
                layout.deferSelfSizing = true
                observe(layout, 1376)
                layout.deferSelfSizing = false
                layout.applyDeferredHeights()
                try require(height(layout) == 1631, "untrusted self-size was queued over a confirmed height")
            }),
            ("snapshot_swap_remaps_pending_and_keys", {
                let layout = makeLayout()
                let a = MessageListItem.wholeMessage(UUID())
                let b = MessageListItem.wholeMessage(UUID())
                layout.setContentKey("A:v1", at: 0)
                layout.setContentKey("B:v1", at: 1)
                observe(layout, 1000, row: 0)
                observe(layout, 2000, row: 1)
                layout.deferSelfSizing = true
                observe(layout, 900, row: 0)
                observe(layout, 1700, row: 1)
                layout.updateCacheForSnapshot(oldIds: [a, b], newIds: [b, a])
                layout.setContentKey("B:v1", at: 0)
                layout.setContentKey("A:v1", at: 1)
                layout.deferSelfSizing = false
                layout.applyDeferredHeights()
                try require(height(layout, row: 0) == 1700 && height(layout, row: 1) == 900,
                            "pending heights were lost or applied to another item's index")
            }),
            ("snapshot_removal_drops_pending", {
                let layout = pendingLayout()
                let a = MessageListItem.wholeMessage(UUID())
                let b = MessageListItem.wholeMessage(UUID())
                layout.deferSelfSizing = false
                observe(layout, 2000, row: 1)
                layout.updateCacheForSnapshot(oldIds: [a, b], newIds: [b])
                layout.collectionView!.itemCount = 1
                layout.applyDeferredHeights()
                try require(height(layout) == 2000, "removed item's pending height contaminated the surviving row")
            }),
            ("changed_content_key_drops_pending", {
                let layout = makeLayout()
                layout.setContentKey("A:v1", at: 0)
                observe(layout, 1631)
                layout.deferSelfSizing = true
                observe(layout, 1376)
                layout.setContentKey("A:v2", at: 0)
                layout.deferSelfSizing = false
                layout.applyDeferredHeights()
                try require(height(layout) == 1631, "old-content pending value survived key replacement")
            }),
            ("same_content_key_keeps_pending", {
                let layout = makeLayout()
                layout.setContentKey("A:v1", at: 0)
                observe(layout, 1631)
                layout.deferSelfSizing = true
                observe(layout, 1376)
                layout.setContentKey("A:v1", at: 0)
                layout.deferSelfSizing = false
                layout.applyDeferredHeights()
                try require(height(layout) == 1376, "unchanged key registration discarded a legitimate shrink")
            }),
            ("width_transition_drops_pending_before_debounce", {
                let layout = pendingLayout()
                changeWidth(layout, to: 390)
                layout.applyDeferredHeights()
                try require(layout.cachedHeight(at: 0) == 1631, "old-width shrink applied before debounced purge")
            }),
            ("reset_cancels_old_width_purge", {
                let layout = pendingLayout()
                changeWidth(layout, to: 390)
                layout.clearHeightCache()
                layout.deferSelfSizing = false
                observe(layout, 700)
                drainWidthCallback()
                try require(layout.cachedHeight(at: 0) == 700, "old width-purge callback erased new session state")
            }),
            ("reset_rebases_width_tracking", {
                let layout = makeLayout()
                changeWidth(layout, to: 390)
                drainWidthCallback()
                layout.collectionView!.bounds.size.width = 428
                layout.clearHeightCache()
                observe(layout, 700)
                changeWidth(layout, to: 390)
                drainWidthCallback()
                try require(layout.cachedHeight(at: 0) == nil, "previous-session width incorrectly suppressed a required purge")
            }),
            ("post_insertion_invalidation_keeps_precalc", {
                let layout = makeLayout()
                layout.setPrecalcHeight(800, at: 0)
                observe(layout, 1000)
                layout.invalidateHeight(at: 0)
                try require(height(layout) == 800, "post-insertion cleanup erased the new item's precalc seed")
            }),
            ("precalc_reseed_keeps_pending", {
                let layout = makeLayout()
                layout.setPrecalcHeight(1631, at: 0)
                layout.deferSelfSizing = true
                observe(layout, 1376)
                layout.setPrecalcHeight(1631, at: 0)
                layout.deferSelfSizing = false
                layout.applyDeferredHeights()
                try require(height(layout) == 1376, "repeated estimate incorrectly cancelled a legitimate observation")
            }),
            ("one_row_invalidation_preserves_other_pending", {
                let layout = makeLayout()
                observe(layout, 1000, row: 0)
                observe(layout, 2000, row: 1)
                layout.deferSelfSizing = true
                observe(layout, 900, row: 0)
                observe(layout, 1700, row: 1)
                layout.invalidateHeight(at: 0)
                layout.deferSelfSizing = false
                layout.applyDeferredHeights()
                try require(height(layout, row: 1) == 1700, "one row's invalidation discarded another row's legitimate pending value")
            }),
            ("inactive_and_zero_width_keep_pending", {
                let layout = pendingLayout()
                UIApplication.shared.applicationState = .inactive
                defer { UIApplication.shared.applicationState = .active }
                let other = CGRect(x: 0, y: 0, width: 390, height: 801)
                try require(!layout.shouldInvalidateLayout(forBoundsChange: other), "background width guard changed")
                UIApplication.shared.applicationState = .active
                try require(!layout.shouldInvalidateLayout(forBoundsChange: .zero), "zero-width guard changed")
                layout.applyDeferredHeights()
                try require(height(layout) == 1376, "invalid width notification discarded legitimate pending work")
            }),
            ("latest_width_target_purges", {
                let layout = makeLayout()
                observe(layout, 1000)
                changeWidth(layout, to: 390)
                changeWidth(layout, to: 400)
                drainWidthCallback()
                try require(layout.cachedHeight(at: 0) == nil, "final width transition never purged old measurements")
            }),
            ("invalid_precalc_is_not_a_protected_reference", {
                for seed in [CGFloat.nan, CGFloat.infinity, CGFloat(-1)] {
                    let layout = makeLayout()
                    layout.setPrecalcHeight(seed, at: 0)
                    layout.deferSelfSizing = true
                    let accepted = layout.shouldInvalidateLayout(forPreferredLayoutAttributes: attributes(90),
                                                                  withOriginalAttributes: attributes(100))
                    try require(accepted, "invalid precalc blocked a valid first measurement")
                }
            }),
            ("genuine_shrink_applies_on_thaw", {
                let layout = makeLayout()
                observe(layout, 1631)
                layout.deferSelfSizing = true
                observe(layout, 1376)
                layout.deferSelfSizing = false
                layout.applyDeferredHeights()
                try require(height(layout) == 1376, "legitimate shrink was lost on thaw")
            }),
            ("zero_height_footer_can_shrink", {
                let layout = makeLayout()
                observe(layout, 32)
                layout.deferSelfSizing = true
                observe(layout, 0)
                layout.deferSelfSizing = false
                layout.applyDeferredHeights()
                try require(height(layout) == 0, "legitimate empty footer remained tall")
            }),
        ]
        var failures: [String] = []
        for (name, test) in tests {
            do { try test(); print("PASS: \(name)") }
            catch { failures.append(name); print("FAIL: \(name): \(error)") }
        }
        let result: [String: Any] = ["tests": tests.count, "failures": failures,
                                   "scope": "actual Swift layout policy; non-rendering platform adapters, not UIKit acceptance"]
        let json = try! JSONSerialization.data(withJSONObject: result, options: [.sortedKeys])
        print("RESULT_JSON: " + String(data: json, encoding: .utf8)!)
        exit(failures.isEmpty ? 0 : 1)
    }
}
