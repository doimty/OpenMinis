import SwiftUI
import UIKit

// Substitutes only for dependencies not used by these layout fixtures.
enum ProbeSettings {
    static var forceLegacy = true
    static var forceLegacyGeometry = true
    static var forcePre26Surface = true
    static var collapseGrid = false
}
struct AppLogger {
    let category: String
    func info(_ text: String) { print("[\(category)] \(text)") }
    func debug(_ text: String) {}
    func warning(_ text: String) { info(text) }
    func error(_ text: String) { info(text) }
}
func noff_try_objc(_ body: () -> Void) -> Bool { body(); return true }
enum AIChatViewModel { static let onAppearTimestamp = CFAbsoluteTimeGetCurrent() }
class SelectableMarkdownTextView: UITextView { func asyncAttachmentRenderSignal() -> Int { 0 } }
class TableAttachment: NSTextAttachment { func invalidateCachedLayoutForWidthChange() {} }
class MinisLayoutManager: NSLayoutManager {}

@MainActor final class RowModel: ObservableObject { @Published var height: CGFloat = 96 }
struct RowContent: View {
    @ObservedObject var model: RowModel
    let index: Int
    var body: some View {
        VStack {
            Text("真实 collection row \(index)")
            Spacer(minLength: 0)
            Button("继续") {}
                .padding(6).background(Color.orange).clipShape(Capsule())
        }
        .frame(maxWidth: .infinity)
        .frame(height: model.height)
        .background(index == 0 ? Color.blue.opacity(0.2) : Color.green.opacity(0.2))
    }
}
@MainActor final class CountingCell: SelfSizingCell {
    static var preferredCalls = 0
    override func preferredLayoutAttributesFitting(_ attrs: UICollectionViewLayoutAttributes) -> UICollectionViewLayoutAttributes {
        Self.preferredCalls += 1
        return super.preferredLayoutAttributesFitting(attrs)
    }
}

@main @MainActor final class ComposerProbeApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    private let root = UIViewController()
    private var mounted: UIViewController?
    private var dataSource: UICollectionViewDiffableDataSource<Int, Int>?
    private var samples: [[String: Any]] = []
    private var checks: [Bool] = []
    private let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("composer-probe", isDirectory: true)

    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UIView.setAnimationsEnabled(false)
        let w = UIWindow(frame: UIScreen.main.bounds)
        window = w
        w.rootViewController = root
        w.makeKeyAndVisible()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        Task { await run() }
        return true
    }

    private func mount(_ vc: UIViewController, width: CGFloat = 428) {
        mounted?.willMove(toParent: nil)
        mounted?.view.removeFromSuperview()
        mounted?.removeFromParent()
        mounted = vc
        root.addChild(vc)
        vc.view.frame = CGRect(x: 0, y: 80, width: min(width, root.view.bounds.width),
                               height: max(400, root.view.bounds.height - 140))
        root.view.addSubview(vc.view)
        vc.didMove(toParent: root)
        root.view.layoutIfNeeded()
    }
    private func settle() async {
        try? await Task.sleep(nanoseconds: 1_000_000_000)
        root.view.layoutIfNeeded()
        try? await Task.sleep(nanoseconds: 100_000_000)
    }
    private func rect(_ r: CGRect?) -> Any {
        guard let r else { return NSNull() }
        return [Double(r.minX), Double(r.minY), Double(r.width), Double(r.height)]
    }
    private func screenshot(_ name: String) {
        guard let window else { return }
        let renderer = UIGraphicsImageRenderer(bounds: window.bounds)
        let image = renderer.image { _ in window.drawHierarchy(in: window.bounds, afterScreenUpdates: true) }
        try? image.pngData()?.write(to: directory.appendingPathComponent(name + ".png"))
    }
    private func sampleComposer(_ name: String, model: ComposerModel, store: FrameStore, expectedVisible: Bool = true) {
        let grid = store.frames["grid"]
        let field = store.frames["field"]
        let chips = model.attachments.compactMap { store.frames["chip-\($0.id)"] }
        let pass: Bool
        if model.attachments.isEmpty { pass = grid == nil }
        else if let grid, let field {
            pass = grid.height >= 69 && store.attachmentHeight >= 69
                && chips.count == model.attachments.count
                && chips.allSatisfy {
                    $0.height >= 63.5 && $0.minY >= grid.minY - 0.5
                        && $0.maxY <= field.minY + 0.5 && !$0.intersects(field)
                }
        } else { pass = false }
        let verdict = pass == expectedVisible
        samples.append(["phase": name, "kind": "composer", "passed": verdict,
                        "observed_visible": pass, "expected_visible": expectedVisible,
                        "outer_geometry_callbacks": store.outerGeometryCallbacks,
                        "attachment_count": model.attachments.count,
                        "reported_grid_height": Double(store.attachmentHeight),
                        "grid": rect(grid), "first_chip": rect(chips.first), "all_chips": chips.map { rect($0) }, "chip_count": chips.count, "field": rect(field),
                        "frame_history": store.frameHistory.mapValues { history in
                            ["count": history.count, "first": rect(history.first), "last": rect(history.last)]
                        }])
        checks.append(verdict)
        screenshot(name)
    }
    private func legacyView(in v: UIView) -> LegacyHostingContentView? {
        if let v = v as? LegacyHostingContentView { return v }
        for child in v.subviews { if let found = legacyView(in: child) { return found } }
        return nil
    }
    private func sampleCollection(_ name: String, vc: MessageListViewController, expected: CGFloat) {
        let cv = vc.collectionView!
        let cells = (0..<2).compactMap { cv.cellForItem(at: IndexPath(item: $0, section: 0)) }
        var rows: [[String: Any]] = []
        var pass = cells.count == 2
        for (index, cell) in cells.enumerated() {
            let host = legacyView(in: cell)?.subviews.first
            let hostRect = host.map { $0.convert($0.bounds, to: cv) }
            let cached = vc.messageListLayout.cachedHeight(at: index)
            let ok = abs(cell.bounds.height - expected) <= 1
                && hostRect.map { $0.maxY <= cell.frame.maxY + 1 } == true
                && cached.map { abs($0 - expected) <= 1 } == true
            pass = pass && ok
            rows.append(["index": index, "frame": rect(cell.frame), "host": rect(hostRect),
                         "cached_height": cached.map { Double($0) } as Any? ?? NSNull(), "passed": ok])
        }
        if cells.count == 2, let host = legacyView(in: cells[0])?.subviews.first {
            pass = pass && host.convert(host.bounds, to: cv).maxY <= cells[1].frame.minY + 0.5
        }
        samples.append(["phase": name, "kind": "collection", "passed": pass,
                        "expected_height": Double(expected), "rows": rows,
                        "preferred_calls": CountingCell.preferredCalls,
                        "cached_count": vc.messageListLayout.debugCachedHeightCount])
        checks.append(pass)
        screenshot(name)
    }

    private func run() async {
        await settle()
        for (name, seed, primitive, legacyFlow, legacyGeometry, mutant) in [
            ("baseline", CGFloat(0), false, true, true, false),
            ("seed70", CGFloat(70), false, true, true, false),
            ("primitive", CGFloat(0), true, true, true, false),
            ("native-flow", CGFloat(0), false, false, true, false),
            ("native-geometry", CGFloat(0), false, true, false, false),
            ("native-both", CGFloat(0), false, false, false, false),
            ("mutant-zero-grid", CGFloat(0), false, true, true, true)
        ] {
            ProbeSettings.forceLegacy = legacyFlow
            ProbeSettings.forceLegacyGeometry = legacyGeometry
            ProbeSettings.collapseGrid = mutant
            let model = ComposerModel()
            let store = FrameStore()
            let host = UIHostingController(rootView: ComposerFixture(vm: model, store: store, seed: seed, primitiveControl: primitive))
            mount(host)
            await settle()
            model.attachments = [InputAttachment()]
            await settle()
            sampleComposer(name + "-one", model: model, store: store, expectedVisible: !mutant)
            if name == "baseline" {
                model.attachments = (0..<8).map { _ in InputAttachment() }
                await settle()
                sampleComposer("baseline-eight", model: model, store: store)
                host.view.frame.size.width = 220
                await settle()
                sampleComposer("baseline-narrow", model: model, store: store)
                model.attachments = []
                await settle()
                sampleComposer("baseline-empty", model: model, store: store)
                model.attachments = [InputAttachment()]
                await settle()
                sampleComposer("baseline-readd", model: model, store: store)
            }
        }

        ProbeSettings.forceLegacy = true
        ProbeSettings.collapseGrid = false
        let model = RowModel()
        let vc = MessageListViewController()
        mount(vc)
        let cv = vc.collectionView!
        cv.register(CountingCell.self, forCellWithReuseIdentifier: "row")
        for index in 0..<2 {
            vc.messageListLayout.setEstimatedHeight(40, at: index)
            vc.messageListLayout.setContentKey("b:fixture-\(index)", at: index)
        }
        dataSource = UICollectionViewDiffableDataSource<Int, Int>(collectionView: cv) { [weak vc] cv, ip, item in
            guard let vc else { return nil }
            let cell = cv.dequeueReusableCell(withReuseIdentifier: "row", for: ip) as! CountingCell
            cell.contentKey = "b:fixture-\(item)"
            cell.applyHostedContent(parent: vc) { RowContent(model: model, index: item) }
            return cell
        }
        var snapshot = NSDiffableDataSourceSnapshot<Int, Int>()
        snapshot.appendSections([0]); snapshot.appendItems([0, 1])
        await dataSource?.apply(snapshot, animatingDifferences: false)
        await settle()
        sampleCollection("collection-initial", vc: vc, expected: 96)
        model.height = 160
        await settle()
        sampleCollection("collection-grow", vc: vc, expected: 160)
        model.height = 56
        await settle()
        sampleCollection("collection-shrink", vc: vc, expected: 56)

        let report: [String: Any] = [
            "os": UIDevice.current.systemVersion,
            "passed": !checks.isEmpty && checks.allSatisfy { $0 }, "samples": samples,
            "limits": "Source-derived production layout/cell paths, forced legacy on iOS26.2. Fixed64 chips and fixed-height row content; not image I/O, actual Resume action, full-app, or iOS15 device acceptance."
        ]
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: directory.appendingPathComponent("report.json"))
            print(String(decoding: data, as: UTF8.self))
        } catch { print("REPORT ERROR: \(error)"); exit(2) }
        exit(checks.allSatisfy { $0 } ? 0 : 1)
    }
}
