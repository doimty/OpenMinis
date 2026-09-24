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
