import SwiftUI
import UIKit

// Substitutes only for dependencies not used by this collection fixture.
enum ProbeSettings {
    static var forceLegacy = true
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
        VStack(alignment: .leading, spacing: 0) {
            Text("real collection row \(index) regular line")
                .font(.body)
                .lineLimit(nil)
            Text("second line keeps a deterministic height")
                .font(.footnote)
                .foregroundColor(.secondary)
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

@main @MainActor final class ObservationProbeApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    private let root = UIViewController()
    private var vc: MessageListViewController?
    private var dataSource: UICollectionViewDiffableDataSource<Int, Int>?
    private let model = RowModel()
    private let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("observation-probe", isDirectory: true)
    private var samples: [[String: Any]] = []
    private var checks: [Bool] = []
    private var runID = ""
    private var variant = ""

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UIView.setAnimationsEnabled(false)
        guard let runID = ProcessInfo.processInfo.environment["PROBE_RUN_ID"], UUID(uuidString: runID) != nil,
              let variant = ProcessInfo.processInfo.environment["PROBE_VARIANT"],
              ["baseline", "candidate"].contains(variant) else {
            print("INVALID: missing run identity"); exit(2)
        }
        self.runID = runID
        self.variant = variant
        let w = UIWindow(frame: UIScreen.main.bounds)
        window = w
        w.rootViewController = root
        w.makeKeyAndVisible()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        DispatchQueue.global().asyncAfter(deadline: .now() + 45) {
            FileHandle.standardError.write(Data("INVALID: observation probe watchdog\n".utf8))
            exit(2)
        }
        Task { await run() }
        return true
    }

    private func mount(_ vc: UIViewController) {
        root.addChild(vc)
        vc.view.frame = CGRect(x: 0, y: 80, width: min(428, root.view.bounds.width),
                               height: max(400, root.view.bounds.height - 140))
        root.view.addSubview(vc.view)
        vc.didMove(toParent: root)
        root.view.layoutIfNeeded()
    }

    private func settle() async {
        try? await Task.sleep(nanoseconds: 700_000_000)
        root.view.layoutIfNeeded()
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
        guard let cv = vc?.collectionView,
              let cell = cv.cellForItem(at: IndexPath(item: index, section: 0)) else { return nil }
        return cell.frame.height
    }

    private func near(_ value: CGFloat?, _ target: CGFloat) -> Bool {
        guard let value else { return false }
        return abs(value - target) <= 1.5
    }

    private func configure(_ height: CGFloat) {
        model.height = height
        guard let vc, let cv = vc.collectionView else { return }
        for index in 0..<2 {
            guard let cell = cv.cellForItem(at: IndexPath(item: index, section: 0)) as? CountingCell else { continue }
            cell.contentKey = "b:fixture-\(index)"
            cell.applyHostedContent(parent: vc) { RowContent(model: model, index: index) }
        }
        cv.collectionViewLayout.invalidateLayout()
        cv.setNeedsLayout()
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
                if let hosted = legacy.subviews.first {
                    row["hostFrame"] = rect(hosted.convert(hosted.bounds, to: cv))
                }
            }
            rows.append(row)
        }
        samples.append(["case": name, "rows": rows,
                        "preferredCalls": CountingCell.preferredCalls,
                        "cachedCount": vc.messageListLayout.debugCachedHeightCount,
                        "expectation": expectation,
                        "hypothesis_match": hypothesis as Any? ?? NSNull(),
                        "note": note])
        if let check { checks.append(check) }
    }

    private func run() async {
        await settle()
        let vc = MessageListViewController()
        self.vc = vc
        mount(vc)
        let cv = vc.collectionView!
        cv.register(CountingCell.self, forCellWithReuseIdentifier: "row")
        for index in 0..<2 {
            vc.messageListLayout.setEstimatedHeight(40, at: index)
            vc.messageListLayout.setContentKey("b:fixture-\(index)", at: index)
        }
        dataSource = UICollectionViewDiffableDataSource<Int, Int>(collectionView: cv) { [weak vc, model = self.model] cv, ip, item in
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

        // Case 1 — real first layout positive control.
        let initial = near(height(0), 96) && near(height(1), 96)
        sample("initial", expectation: "real-height-96", hypothesis: nil,
               check: initial, note: "both rows must settle at the real 96pt height")

        // Case 2 — same-content generic invalidation (hypothesis C1/C2).
        if let cell = cv.cellForItem(at: IndexPath(item: 0, section: 0)) as? CountingCell {
            cell.clearCachedHeight()
        }
        vc.messageListLayout.invalidateHeight(at: 0)
        cv.collectionViewLayout.invalidateLayout()
        cv.setNeedsLayout()
        await settle()
        let starved = near(height(0), 40)
        let recovered = near(height(0), 96)
        sample("clear-same-content",
               expectation: variant == "baseline" ? "starve-to-estimate" : "recover-via-preserved-legacy",
               hypothesis: variant == "baseline" ? starved : recovered,
               note: "cell0 cleared+hInvalidated+lInvalidated with unchanged content; row1 untouched")

        // Case 3 — real height change as recovery control.
        configure(160)
        await settle()
        let grown = near(height(0), 160) && near(height(1), 160)
        sample("recovery-grow", expectation: "recover-to-160", hypothesis: nil,
               check: grown, note: "fresh applyHostedContent must recover both rows in both variants")

        // Case 4 — identical reconfigure (hypothesis C4).
        configure(160)
        await settle()
        let same = near(height(0), 160) && near(height(1), 160)
        sample("same-size-reconfigure", expectation: "recover-same-size", hypothesis: same,
               note: "identical height re-applied; observe whether the observation edge is re-delivered")

        // Case 5 — stale-seed invalidation (hypothesis C2).
        if let cell = cv.cellForItem(at: IndexPath(item: 1, section: 0)) as? CountingCell {
            cell.seedMeasuredHeight(999, width: cv.bounds.width)
            cell.clearCachedHeight()
        }
        vc.messageListLayout.invalidateHeight(at: 1)
        cv.collectionViewLayout.invalidateLayout()
        cv.setNeedsLayout()
        await settle()
        let staleSeed = near(height(1), 999)
        let seedCleared = near(height(1), 160)
        sample("seed-invalidation",
               expectation: variant == "baseline" ? "stale-seed-wins" : "seed-cleared",
               hypothesis: variant == "baseline" ? staleSeed : seedCleared,
               note: "seed 999 then clear; baseline must show the seed survives clearCachedHeight")

        let controlsPassed = checks.count == 2 && checks.allSatisfy { $0 }
        let report: [String: Any] = [
            "os": UIDevice.current.systemVersion,
            "variant": variant,
            "runID": runID,
            "controlsPassed": controlsPassed,
            "passed": controlsPassed,
            "samples": samples,
            "limits": "Source-derived production collection/layout/legacy-hosting paths, forced legacy on iOS26.2 runtime. Observation probe only; no synchronous measurement added, no UI layout verdict.",
        ]
        do {
            let target = directory.appendingPathComponent(runID, isDirectory: true)
            try FileManager.default.createDirectory(at: target, withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: target.appendingPathComponent("report.json"), options: .atomic)
            print(String(decoding: data, as: UTF8.self))
        } catch {
            print("REPORT ERROR: \(error)"); exit(2)
        }
        exit(controlsPassed ? 0 : 1)
    }
}