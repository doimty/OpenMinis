// Appended only to a disposable Debug build by prepare_device_probe.py.
// All renderer/text/host/cell/layout classes are the COMPLETE app sources.
#if DEBUG
import CryptoKit
import Foundation
import SwiftUI
import UIKit

private struct NativeHeightFixture {
    let markdown: String
    var digest: String { SHA256.hash(data: Data(markdown.utf8)).map { String(format: "%02x", $0) }.joined() }

    static func code(lines: Int) -> NativeHeightFixture {
        let code = (0..<lines).map { "let item\($0) = \"finite width production renderer\"" }.joined(separator: "\n")
        return NativeHeightFixture(markdown: """
        A neutral **native layout** fixture. This paragraph wraps at the real viewport width and contains no private conversation.

        ```swift
        \(code)
        ```

        The final paragraph must remain present. The same unchanged Markdown is used for the independent reference and the subject row.
        """)
    }

    static let plain = NativeHeightFixture(markdown: String(repeating: "A plain paragraph rendered through the real Markdown component. ", count: 5))
}

private struct NativeHeightRow: View {
    let fixture: NativeHeightFixture
    var body: some View {
        SelectableMarkdownView(markdown: fixture.markdown, cachedContent: MarkdownContent(fixture.markdown))
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12)
    }
}

@MainActor
private final class NativeFrameWaiter: NSObject {
    private var link: CADisplayLink?
    private var remaining = 0
    private var continuation: CheckedContinuation<Void, Error>?
    private var timeoutTask: Task<Void, Never>?

    func wait(_ frames: Int) async throws {
        try await withCheckedThrowingContinuation { (value: CheckedContinuation<Void, Error>) in
            continuation = value
            remaining = frames
            let displayLink = CADisplayLink(target: self, selector: #selector(tick))
            link = displayLink
            displayLink.add(to: .main, forMode: .common)
            timeoutTask = Task { [weak self] in
                do { try await Task.sleep(nanoseconds: 10_000_000_000) } catch { return }
                self?.finish(NSError(domain: "NativeHeightProbe", code: 1,
                                    userInfo: [NSLocalizedDescriptionKey: "No display frames / app inactive"]))
            }
        }
    }

    @objc private func tick() {
        remaining -= 1
        if remaining <= 0 { finish(nil) }
    }

    private func finish(_ error: Error?) {
        guard let value = continuation else { return }
        continuation = nil
        link?.invalidate()
        link = nil
        timeoutTask?.cancel()
        timeoutTask = nil
        if let error { value.resume(throwing: error) } else { value.resume() }
    }
}

@MainActor
private final class NativeHeightProbeController: UIViewController {
    private let statusLabel = UILabel()
    private let shareButton = UIButton(type: .system)
    private let list = MessageListViewController()
    private var dataSource: UICollectionViewDiffableDataSource<Int, Int>?
    private var reuseIdentifier = "native-probe-row"
    private var fixture = NativeHeightFixture.code(lines: 3)
    private var cases: [[String: Any]] = []
    private var reportURL: URL?
    private let baselineCommit = "2f21e242df71d63576682f8ac61c50a097f3a5ae"

    override var supportedInterfaceOrientations: UIInterfaceOrientationMask { .portrait }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        statusLabel.numberOfLines = 3
        statusLabel.font = .systemFont(ofSize: 14)
        statusLabel.text = "Minis 原生布局测试\n合成内容，不读取或修改正常 Minis 的聊天。"
        view.addSubview(statusLabel)
        shareButton.setTitle("分享测试报告", for: .normal)
        shareButton.isEnabled = false
        shareButton.addTarget(self, action: #selector(shareReport), for: .touchUpInside)
        view.addSubview(shareButton)
        addChild(list)
        view.addSubview(list.view)
        list.didMove(toParent: self)
        list.collectionView.isUserInteractionEnabled = false
        list.collectionView.contentInsetAdjustmentBehavior = .never
        list.collectionView.register(SelfSizingCell.self, forCellWithReuseIdentifier: reuseIdentifier)
        dataSource = UICollectionViewDiffableDataSource<Int, Int>(collectionView: list.collectionView) { [weak self] cv, path, _ in
            guard let self else { return nil }
            let cell = cv.dequeueReusableCell(withReuseIdentifier: self.reuseIdentifier, for: path) as! SelfSizingCell
            let currentFixture = self.fixture
            cell.contentKey = "native-probe-" + currentFixture.digest
            self.list.messageListLayout.setContentKey(cell.contentKey!, at: path.item)
            cell.applyHostedContent(parent: self.list) { NativeHeightRow(fixture: currentFixture) }
            ReentryDiagnostics.shared.record(kind: "configure", owner: cv, subject: cell,
                index: path.item, generation: cell.reentryDiagnosticGeneration, phase: "probe-configure",
                values: ["height": Double(cell.bounds.height), "width": Double(cv.bounds.width)])
            return cell
        }
        Task { [weak self] in await self?.runProbe() }
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        let safe = view.safeAreaInsets
        statusLabel.frame = CGRect(x: 12, y: safe.top + 6, width: view.bounds.width - 24, height: 64)
        shareButton.frame = CGRect(x: 12, y: view.bounds.height - safe.bottom - 48,
                                   width: view.bounds.width - 24, height: 40)
        list.view.frame = CGRect(x: 0, y: safe.top + 80, width: view.bounds.width,
                                 height: max(100, view.bounds.height - safe.top - safe.bottom - 142))
    }

    private func frames(_ count: Int = 45) async throws {
        let waiter = NativeFrameWaiter()
        try await waiter.wait(count)
        guard UIApplication.shared.applicationState == .active else {
            throw problem("App left foreground")
        }
    }

    private func problem(_ text: String) -> NSError {
        NSError(domain: "NativeHeightProbe", code: 2, userInfo: [NSLocalizedDescriptionKey: text])
    }

    private func first<T: UIView>(_ type: T.Type, in view: UIView) -> T? {
        if let result = view as? T { return result }
        for child in view.subviews { if let result = first(type, in: child) { return result } }
        return nil
    }

    private func rect(_ r: CGRect) -> [Double] {
        [Double(r.minX), Double(r.minY), Double(r.width), Double(r.height)]
    }

    // Reference-only measurement. Never called on the subject's text view.
    private func reference(for fixture: NativeHeightFixture) async throws -> [String: Any] {
        let referenceHost = UIHostingController(rootView: NativeHeightRow(fixture: fixture))
        addChild(referenceHost)
        referenceHost.view.frame = CGRect(x: view.bounds.width + 32, y: 100,
                                          width: list.collectionView.bounds.width, height: 2000)
        view.addSubview(referenceHost.view)
        referenceHost.didMove(toParent: self)
        defer {
            referenceHost.willMove(toParent: nil)
            referenceHost.view.removeFromSuperview()
            referenceHost.removeFromParent()
        }
        try await frames(20)
        guard let text = first(SelectableMarkdownTextView.self, in: referenceHost.view),
              text.window != nil, text.textStorage.length > 0,
              text.bounds.width.isFinite, text.bounds.width > 1 else {
            throw problem("Independent production renderer did not establish text/width")
        }
        let width = text.bounds.width
        let height = text.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude)).height
        guard height.isFinite, height > 1 else { throw problem("Invalid independent reference") }
        return ["width": Double(width), "height": Double(ceil(height)), "textLength": text.textStorage.length]
    }

    private func apply(_ items: [Int]) async {
        guard let dataSource else { return }
        var snapshot = NSDiffableDataSourceSnapshot<Int, Int>()
        snapshot.appendSections([0])
        snapshot.appendItems(items)
        await withCheckedContinuation { (value: CheckedContinuation<Void, Never>) in
            dataSource.apply(snapshot, animatingDifferences: false) { value.resume() }
        }
    }

    private func marker(_ name: String, edge: Double) {
        ReentryDiagnostics.shared.record(kind: "probe-marker", owner: list.collectionView,
            subject: self, phase: name, values: ["edge": edge])
    }

    // PASSIVE observation. No intrinsicContentSize, sizeThatFits or layout call.
    private func snapshot() throws -> [String: Any] {
        guard let cell = list.collectionView.cellForItem(at: IndexPath(item: 0, section: 0)) as? SelfSizingCell,
              let host = first(LegacyHostingContentView.self, in: cell),
              let hostView = host.subviews.first,
              let text = first(SelectableMarkdownTextView.self, in: cell) else {
            throw problem("Visible production host/cell/text missing")
        }
        return ["cellFrame": rect(cell.frame), "hostFrame": rect(hostView.frame),
                "textBounds": rect(text.bounds), "textLength": text.textStorage.length,
                "inWindow": cell.window != nil && text.window != nil && !cell.isHidden && !text.isHidden
                    && cell.alpha > 0 && text.alpha > 0,
                "cellID": ReentryDiagnostics.identity(cell),
                "textID": ReentryDiagnostics.identity(text),
                "configGeneration": cell.reentryDiagnosticGeneration,
                "layoutCachedHeight": list.messageListLayout.cachedHeight(at: 0).map { Double($0) } as Any? ?? NSNull()]
    }

    private func finishCase(_ name: String, reference: [String: Any]) throws {
        let sample = try snapshot()
        marker(name, edge: 1)
        cases.append(["name": name, "fixtureSHA256": fixture.digest,
                      "collectionID": ReentryDiagnostics.identity(list.collectionView),
                      "reference": reference, "snapshot": sample])
    }

    private func runProbe() async {
        let sourceCommit = (Bundle.main.object(forInfoDictionaryKey: "MinisDiagnosticCommit") as? String) ?? "unknown"
        var captureError: String?
        do {
            guard (Bundle.main.object(forInfoDictionaryKey: "MinisNativeHeightProbe") as? Bool) == true,
                  ReentryDiagnostics.enabled else { throw problem("Test-only bundle stamp missing") }
            guard #unavailable(iOS 16.0) else { throw problem("This device probe requires the actual iOS 15 legacy path") }
            try await frames(15)
            let short = NativeHeightFixture.code(lines: 3)
            let long = NativeHeightFixture.code(lines: 12)
            let shortReference = try await reference(for: short)
            let longReference = try await reference(for: long)
            let plainReference = try await reference(for: .plain)

            fixture = short
            statusLabel.text = "1/6 初始真实渲染"
            marker("initial", edge: 0)
            list.messageListLayout.setEstimatedHeight(44, at: 0)
            await apply([0])
            try await frames()
            try finishCase("initial", reference: shortReference)

            statusLabel.text = "2/6 同内容重新挂载，观察短暂高度回退"
            marker("deferred-reentry", edge: 0)
            list.messageListLayout.deferSelfSizing = true
            await apply([])
            try await frames(3)
            // The first device run kept the same already-measured UITextView
            // in the original reuse pool, so it never exercised unset width
            // during deferSelfSizing. Use a new UIKit reuse pool for THIS case
            // only. The class, content, width, production sizing and caches are
            // unchanged; UIKit constructs a genuinely new cell/host/text tree.
            reuseIdentifier = "native-probe-fresh-" + UUID().uuidString
            list.collectionView.register(SelfSizingCell.self, forCellWithReuseIdentifier: reuseIdentifier)
            list.messageListLayout.invalidateHeight(at: 0)
            list.messageListLayout.setPrecalcHeight(CGFloat(shortReference["height"] as! Double), at: 0)
            await apply([0])
            try await frames()
            try finishCase("deferred-reentry", reference: shortReference)

            statusLabel.text = "3/6 解除暂缓，等待真实校正"
            marker("deferred-settle", edge: 0)
            list.messageListLayout.deferSelfSizing = false
            if let cell = list.collectionView.cellForItem(at: IndexPath(item: 0, section: 0)),
               let text = first(SelectableMarkdownTextView.self, in: cell) {
                text.consumeDeferredCorrectionIfNeeded()
            }
            list.messageListLayout.applyDeferredHeights()
            list.messageListLayout.invalidateLayout()
            try await frames()
            try finishCase("deferred-settle", reference: shortReference)

            statusLabel.text = "4/6 真实代码附件增长"
            marker("attachment-growth", edge: 0)
            fixture = long
            if let cell = list.collectionView.cellForItem(at: IndexPath(item: 0, section: 0)) as? SelfSizingCell {
                cell.contentKey = "native-probe-" + long.digest
                list.messageListLayout.setContentKey(cell.contentKey!, at: 0)
                cell.applyHostedContent(parent: list) { NativeHeightRow(fixture: long) }
                ReentryDiagnostics.shared.record(kind: "configure", owner: list.collectionView, subject: cell,
                    index: 0, generation: cell.reentryDiagnosticGeneration, phase: "probe-configure")
            }
            list.messageListLayout.invalidateLayout()
            try await frames()
            try finishCase("attachment-growth", reference: longReference)

            statusLabel.text = "5/6 正常复用控制"
            marker("reuse", edge: 0)
            await apply([])
            try await frames(3)
            await apply([0])
            try await frames()
            try finishCase("reuse", reference: longReference)

            statusLabel.text = "6/6 纯文本控制"
            marker("plain-control", edge: 0)
            fixture = .plain
            await apply([])
            try await frames(3)
            list.messageListLayout.invalidateHeight(at: 0)
            await apply([0])
            try await frames()
            try finishCase("plain-control", reference: plainReference)
        } catch { captureError = error.localizedDescription }

        do {
            let trace = try ReentryDiagnostics.shared.nativeProbeReadEvents()
            for index in cases.indices {
                let name = cases[index]["name"] as! String
                let boundaries = trace.filter { ($0["kind"] as? String) == "probe-marker" && ($0["phase"] as? String) == name }
                guard let first = boundaries.first, let last = boundaries.last, boundaries.count == 2,
                      let begin = first["seq"], let end = last["seq"] else { throw problem("Probe marker missing / trace capped") }
                cases[index]["startSeq"] = begin
                cases[index]["endSeq"] = end
            }
            var report: [String: Any] = [
                "schema": 1, "kind": "minis-provisional-height-device-probe",
                "runID": ReentryDiagnostics.shared.nativeProbeRunID,
                "sourceCommit": sourceCommit, "baselineCommit": baselineCommit,
                "os": UIDevice.current.systemVersion,
                "legacyPath": ProcessInfo.processInfo.operatingSystemVersion.majorVersion == 15,
                "cases": cases, "trace": trace, "requiresFreshReentry": true,
                "limits": "Full production components, neutral input, policy-driven local re-entry; not real finger motion or original conversation acceptance. Validator decides verdict."
            ]
            if let captureError { report["captureError"] = captureError }
            let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("provisional-height-probe", isDirectory: true)
                .appendingPathComponent(ReentryDiagnostics.shared.nativeProbeRunID, isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let url = directory.appendingPathComponent("report.json")
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: url, options: .atomic)
            reportURL = url
            shareButton.isEnabled = true
            statusLabel.text = captureError == nil
                ? "采集完成。请分享报告供独立判定。\n这不是修复通过，也不影响正常 Minis。"
                : "采集未通过：\(captureError!)\n仍可分享报告。"
        } catch {
            statusLabel.text = "INVALID：报告写出失败。\n\(error.localizedDescription)"
        }
    }

    @objc private func shareReport() {
        guard let reportURL else { return }
        let share = UIActivityViewController(activityItems: [reportURL], applicationActivities: nil)
        share.popoverPresentationController?.sourceView = shareButton
        share.popoverPresentationController?.sourceRect = shareButton.bounds
        present(share, animated: true)
    }
}

@main
@MainActor
final class NativeProvisionalHeightApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = NativeHeightProbeController()
        self.window = window
        window.makeKeyAndVisible()
        return true
    }
}
#endif
