// Non-rendering platform adapters for the complete production layout class.
// These do not emulate UIKit's sizing/layout scheduler. Tests explicitly call
// production admission, cache and preparation APIs; real UIKit remains a device gate.
import Foundation

typealias CFTimeInterval = Double
func CACurrentMediaTime() -> CFTimeInterval { ProcessInfo.processInfo.systemUptime }

struct AppLogger {
    init(category: String) {}
    func info(_ message: String) {}
    func debug(_ message: String) {}
}

extension IndexPath {
    init(item: Int, section: Int) { self.init(indexes: [section, item]) }
    var item: Int { self[1] }
}

class UICollectionViewLayoutAttributes {
    let indexPath: IndexPath
    var frame = CGRect.zero
    var size: CGSize {
        get { frame.size }
        set { frame.size = newValue }
    }
    init(forCellWith indexPath: IndexPath) { self.indexPath = indexPath }
}

class UICollectionViewLayoutInvalidationContext {
    var contentOffsetAdjustment = CGPoint.zero
}

class UICollectionView {
    var bounds = CGRect(x: 0, y: 0, width: 428, height: 801)
    var contentOffset = CGPoint.zero
    var isTracking = false
    var isDecelerating = false
    var numberOfSections = 1
    var itemCount = 2
    func numberOfItems(inSection section: Int) -> Int { itemCount }
}

class UICollectionViewLayout {
    var collectionView: UICollectionView?
    var collectionViewContentSize: CGSize { .zero }
    func prepare() {}
    func invalidateLayout() {}
    func layoutAttributesForElements(in rect: CGRect) -> [UICollectionViewLayoutAttributes]? { nil }
    func layoutAttributesForItem(at indexPath: IndexPath) -> UICollectionViewLayoutAttributes? { nil }
    func shouldInvalidateLayout(forBoundsChange newBounds: CGRect) -> Bool { false }
    func shouldInvalidateLayout(forPreferredLayoutAttributes preferred: UICollectionViewLayoutAttributes,
                                withOriginalAttributes original: UICollectionViewLayoutAttributes) -> Bool { false }
    func invalidationContext(forPreferredLayoutAttributes preferred: UICollectionViewLayoutAttributes,
                             withOriginalAttributes original: UICollectionViewLayoutAttributes) -> UICollectionViewLayoutInvalidationContext {
        UICollectionViewLayoutInvalidationContext()
    }
}

class UIApplication {
    enum State { case active, inactive }
    static let shared = UIApplication()
    var applicationState: State = .active
}
