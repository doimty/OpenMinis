import SwiftUI
import UIKit

enum ProbeSettings { static var forceLegacy = true }
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
        VStack(alignment: .leading, spacing: 0) {
            Text("real collection row \(index) regular line").font(.body).lineLimit(nil)
            Text("second line keeps a deterministic height").font(.footnote).foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .frame(height: model.height)
        .padding(.horizontal, 12)
        .background(index == 0 ? Color.blue.opacity(0.15) : Color.orange.opacity(0.15))
    }
}

@MainActor final class CountingCell: SelfSizingCell {
    static var preferredCalls = 0
    override func preferredLayoutAttributesFitting(_ attrs: UICollectionViewLayoutAttributes) -> UICollectionViewLayoutAttributes {
        Self.preferredCalls += 1
        return super.preferredLayoutAttributesFitting(attrs)
    }
}

@main @MainActor final class RevisionProbeApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    private let root = UIViewController()
    private var vc: MessageListViewController?
    private var dataSource: UICollectionViewDiffableDataSource<Int, Int>?
    private let model = RowModel()
    private let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("legacy-revision-probe", isDirectory: true)
    private var samples: [[String: Any]] = []
    private var checks: [Bool] = []
    private var runID = ""
    private var variant = ""

    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UIView.setAnimationsEnabled(false)
        guard let runID = ProcessInfo.processInfo.environment["PROBE_RUN_ID"], UUID(uuidString: runID) != nil,
              let variant = ProcessInfo.processInfo.environment["PROBE_VARIANT"],
              ["baseline", "c1", "c3"].contains(variant) else { print("INVALID: missing run identity"); exit(2) }
        self.runID = runID; self.variant = variant
        let w = UIWindow(frame: UIScreen.main.bounds); window = w; w.rootViewController = root; w.makeKeyAndVisible()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        DispatchQueue.global().asyncAfter(deadline: .now() + 55) {
            FileHandle.standardError.write(Data("INVALID: revision probe watchdog\n".utf8)); exit(2)
        }
        Task { await run() }
        return true
    }

    private func mount(_ vc: UIViewController) {
        root.addChild(vc)
        vc.view.frame = CGRect(x: 0, y: 80, width: min(428, root.view.bounds.width), height: max(400, root.view.bounds.height - 140))
        root.view.addSubview(vc.view); vc.didMove(toParent: root); root.view.layoutIfNeeded()
    }
    private func settle() async {
        try? await Task.sleep(nanoseconds: 700_000_000); root.view.layoutIfNeeded()
        try? await Task.sleep(nanoseconds: 150_000_000)
    }
    private func rect(_ r: CGRect?) -> Any {
        guard let r else { return NSNull() }
        return [Double(r.minX), Double(r.minY), Double(r.width), Double(r.height)]
    }
    private func legacyView(in v: UIView) -> LegacyHostingContentView? {
        if let v = v as? LegacyHostingContentView { return v }
        for child in v.subviews { if let found = legacyView(in: child) { return found } }
        return nil
    }
    private func height(_ index: Int) -> CGFloat? {
        guard let cv = vc?.collectionView, let cell = cv.cellForItem(at: IndexPath(item: index, section: 0)) else { return nil }
        return cell.frame.height
    }
    private func near(_ value: CGFloat?, _ target: CGFloat) -> Bool {
        guard let value else { return false }; return abs(value - target) <= 1.5
    }
    private func configure(_ height: CGFloat) {
        model.height = height
        guard let vc, let cv = vc.collectionView else { return }
        for index in 0..<2 {
            guard let cell = cv.cellForItem(at: IndexPath(item: index, section: 0)) as? CountingCell else { continue }
            cell.contentKey = "b:fixture-\(index)"
            cell.applyHostedContent(parent: vc) { RowContent(model: model, index: index) }
        }
        cv.collectionViewLayout.invalidateLayout(); cv.setNeedsLayout()
    }
    private func sample(_ name: String, expectation: String, hypothesis: Bool?, check: Bool? = nil, note: String = "") {
        guard let vc, let cv = vc.collectionView else { return }
        var rows: [[String: Any]] = []
        for index in 0..<2 {
            guard let cell = cv.cellForItem(at: IndexPath(item: index, section: 0)) as? CountingCell else { continue }
            var row: [String: Any] = ["index": index, "cellFrame": rect(cell.frame),
                "cachedHeight": vc.messageListLayout.cachedHeight(at: index).map { Double($0) } as Any? ?? NSNull(),
                "cache": cell.probeCacheSnapshot.json]
            if let legacy = legacyView(in: cell) {
                row["legacyContainerFrame"] = rect(legacy.convert(legacy.bounds, to: cv))
                if let hosted = legacy.subviews.first { row["hostFrame"] = rect(hosted.convert(hosted.bounds, to: cv)) }
            }
            rows.append(row)
        }
        samples.append(["case": name, "rows": rows, "preferredCalls": CountingCell.preferredCalls,
                        "cachedCount": vc.messageListLayout.debugCachedHeightCount,
                        "expectation": expectation, "hypothesis_match": hypothesis as Any? ?? NSNull(), "note": note])
        if let check { checks.append(check) }
    }

    private func run() async {
        await settle()
        let vc = MessageListViewController(); self.vc = vc; mount(vc)
        let cv = vc.collectionView!; cv.register(CountingCell.self, forCellWithReuseIdentifier: "row")
        for index in 0..<2 { vc.messageListLayout.setEstimatedHeight(40, at: index); vc.messageListLayout.setContentKey("b:fixture-\(index)", at: index) }
        dataSource = UICollectionViewDiffableDataSource<Int, Int>(collectionView: cv) { [weak vc, model = self.model] cv, indexPath, item in
            guard let vc else { return nil }
            let cell = cv.dequeueReusableCell(withReuseIdentifier: "row", for: indexPath) as! CountingCell
            cell.contentKey = "b:fixture-\(item)"; cell.applyHostedContent(parent: vc) { RowContent(model: model, index: item) }; return cell
        }
        var snapshot = NSDiffableDataSourceSnapshot<Int, Int>(); snapshot.appendSections([0]); snapshot.appendItems([0, 1])
        await dataSource?.apply(snapshot, animatingDifferences: false); await settle()
        let initial = near(height(0), 96) && near(height(1), 96)
        sample("initial", expectation: "real-height-96", hypothesis: nil, check: initial, note: "initial real sizing control")

        if let cell = cv.cellForItem(at: IndexPath(item: 0, section: 0)) as? CountingCell { cell.clearCachedHeight() }
        vc.messageListLayout.invalidateHeight(at: 0); cv.collectionViewLayout.invalidateLayout(); cv.setNeedsLayout(); await settle()
        sample("clear-same-content", expectation: variant == "baseline" ? "starve-to-estimate" : "recover-via-preserved-legacy",
               hypothesis: variant == "baseline" ? near(height(0), 40) : near(height(0), 96), note: "C1 same-content clear")

        configure(160); await settle()
        sample("recovery-grow", expectation: "recover-to-160", hypothesis: nil,
               check: near(height(0), 160) && near(height(1), 160), note: "real height-change control")

        configure(160); await settle()
        let same = (0..<2).allSatisfy { index in
            guard let cell = cv.cellForItem(at: IndexPath(item: index, section: 0)) as? CountingCell else { return false }
            return near(height(index), 160) && near(cell.probeCacheSnapshot.legacyMeasuredSize?.height, 160)
        }
        sample("same-size-reconfigure", expectation: "same-size-observation-rearmed", hypothesis: same,
               note: "same frame is insufficient; current-generation legacy observation must be present")

        configure(120); await settle()
        let seedReady = (0..<2).allSatisfy { index in
            guard let cell = cv.cellForItem(at: IndexPath(item: index, section: 0)) as? CountingCell else { return false }
            return near(height(index), 120) && near(cell.probeCacheSnapshot.legacyMeasuredSize?.height, 120)
        }
        sample("seed-recovery-control", expectation: "fresh-observation-120", hypothesis: nil, check: seedReady,
               note: "independent fresh observation before C2")

        if let cell = cv.cellForItem(at: IndexPath(item: 1, section: 0)) as? CountingCell {
            cell.seedMeasuredHeight(176, width: cv.bounds.width); cell.clearCachedHeight()
        }
        vc.messageListLayout.invalidateHeight(at: 1); cv.collectionViewLayout.invalidateLayout(); cv.setNeedsLayout(); await settle()
        sample("seed-invalidation", expectation: variant == "baseline" ? "stale-seed-wins" : "seed-cleared",
               hypothesis: variant == "baseline" ? near(height(1), 176) : near(height(1), 120),
               note: "bounded seed176 after fresh120 control")

        let controlsPassed = checks.count == 3 && checks.allSatisfy { $0 }
        let report: [String: Any] = ["os": UIDevice.current.systemVersion, "variant": variant, "runID": runID,
            "controlsPassed": controlsPassed, "passed": controlsPassed, "samples": samples,
            "limits": "Source-derived collection/legacy observation only; forced legacy on iOS26.2, no synchronous measurement or UI verdict."]
        do {
            let target = directory.appendingPathComponent(runID, isDirectory: true)
            try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: target.appendingPathComponent("report.json"), options: .atomic)
            print(String(decoding: data, as: UTF8.self))
        } catch { print("REPORT ERROR: \(error)"); exit(2) }
        exit(controlsPassed ? 0 : 1)
    }
}