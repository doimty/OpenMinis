// Appended only to a disposable Debug build by prepare_device_probe.py.
// All renderer/text/host/cell/layout classes are the COMPLETE app sources.
#if DEBUG
import CryptoKit
import Foundation
import SwiftUI
import UIKit
import UniformTypeIdentifiers

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

// The old small fixture driver above remains available for historical reports.
// The replay below uses the real production list, models, caches and gestures.
private struct NativeReplayList: View {
    @ObservedObject var vm: AIChatViewModel
    var body: some View {
        CollectionViewMessageListV3(
            vm: vm, inputFocused: false, onRetryMessage: nil, onRetryLast: nil,
            onEdit: nil, onWithdraw: nil, onResume: nil, onStop: nil, onCompact: nil,
            maxContentWidth: 0, floatingBarHeight: 60, inputBarHeight: 50
        )
        .environmentObject(vm)
    }
}

@MainActor
private final class NativeReplayImportController: UIViewController, UIDocumentPickerDelegate {
    private let status = UILabel()
    private let button = UIButton(type: .system)

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "Minis 真实列表回放"
        view.backgroundColor = .systemBackground
        status.numberOfLines = 0
        status.text = "导入本地会话 JSON。\n原文仅在这个独立 App 内处理，不上传。\n\n打开后先等内容稳定，再点“开始采集”，像原来一样上下滑动。需要时可点“重入”。完成后点“结束分享”。\n\n这是诊断，不是修复版。"
        status.translatesAutoresizingMaskIntoConstraints = false
        button.setTitle("选择本地会话 JSON", for: .normal)
        button.addTarget(self, action: #selector(selectFile), for: .touchUpInside)
        button.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(status)
        view.addSubview(button)
        NSLayoutConstraint.activate([
            status.leadingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.leadingAnchor, constant: 20),
            status.trailingAnchor.constraint(equalTo: view.safeAreaLayoutGuide.trailingAnchor, constant: -20),
            status.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 32),
            button.centerXAnchor.constraint(equalTo: view.centerXAnchor),
            button.topAnchor.constraint(equalTo: status.bottomAnchor, constant: 24)
        ])
    }

    @objc private func selectFile() {
        let picker = UIDocumentPickerViewController(forOpeningContentTypes: [.json], asCopy: true)
        picker.allowsMultipleSelection = false
        picker.delegate = self
        present(picker, animated: true)
    }

    func documentPicker(_ controller: UIDocumentPickerViewController, didPickDocumentsAt urls: [URL]) {
        guard let url = urls.first else { return }
        let access = url.startAccessingSecurityScopedResource()
        defer { if access { url.stopAccessingSecurityScopedResource() } }
        do {
            let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
            guard size > 0 && size <= 2_000_000 else { throw failure("会话文件为空或超过 2 MB") }
            let data = try Data(contentsOf: url)
            guard data.count <= 2_000_000,
                  let root = try JSONSerialization.jsonObject(with: data) as? [[String: Any]] else {
                throw failure("会话 JSON 格式不正确")
            }
            // Accept the original single-session export OR its messages array.
            let entries: [[String: Any]]
            if root.count == 1, let messages = root[0]["messages"] as? [[String: Any]] {
                entries = messages
            } else { entries = root }
            guard !entries.isEmpty && entries.count <= 500,
                  entries.allSatisfy({ entry in
                      guard let role = entry["role"] as? String,
                            role == "user" || role == "assistant",
                            let parts = entry["parts"] as? [[String: Any]] else { return false }
                      return parts.allSatisfy { $0["type"] is String }
                  }) else { throw failure("会话必须包含 1 至 500 条 user/assistant 消息") }
            let vm = AIChatViewModel()
            let labSession = UUID().uuidString
            var uiMessages: [ChatMessage] = []
            var currentAssistant: ChatMessage?
            var omittedMedia = 0
            // Use the actual persisted-row converter (system-reminder stripping,
            // long-text splitting, reasoning and tool kinds), then mirror the
            // consecutive-assistant fold of loadSession. SessionDataSimulator
            // alone loses reasoning and treats each raw loop iteration as a turn.
            for (index, entry) in entries.enumerated() {
                var parts: [ContentPart] = []
                for part in entry["parts"] as! [[String: Any]] {
                    switch part["type"] as? String {
                    case "text":
                        guard let text = part["text"] as? String else { throw failure("文本字段损坏") }
                        parts.append(.text(text))
                    case "tool_use":
                        guard let name = part["name"] as? String else { throw failure("工具名称缺失") }
                        let input: String
                        if let raw = part["input"] as? String { input = raw }
                        else if let object = part["input"] as? [String: Any],
                                let encoded = String(data: try JSONSerialization.data(withJSONObject: object), encoding: .utf8) {
                            input = encoded
                        } else { throw failure("工具输入字段损坏") }
                        parts.append(.toolUse(ToolUse(toolUseId: UUID().uuidString, name: name, input: input,
                            description: part["description"] as? String, thoughtSignature: nil)))
                    case "media":
                        // The clean export carries only a note, not media bytes
                        // or a resolvable MediaRef. Do not invent or fetch them.
                        omittedMedia += 1
                    default: throw failure("导出含未支持的内容类型，不能静默省略")
                    }
                }
                var usage: StoredTokenUsage?
                if let value = entry["tokenUsage"] as? [String: Any] {
                    usage = try JSONDecoder().decode(StoredTokenUsage.self,
                        from: JSONSerialization.data(withJSONObject: value))
                }
                let raw = RawMessage(id: UUID().uuidString, sessionId: labSession,
                    role: entry["role"] as? String == "user" ? .user : .assistant,
                    parts: parts, createdAt: Date(timeIntervalSince1970: 0), tokenUsage: usage,
                    reasoningContent: entry["reasoning"] as? String, sortOrder: index)
                if raw.isInternalBridge { continue }
                let message = raw.toChatMessage(mediaResolver: { _ in
                    FileManager.default.temporaryDirectory.appendingPathComponent("unavailable-export-media")
                }, showThinking: true)
                if raw.role == .assistant, let current = currentAssistant {
                    current.blocks.append(contentsOf: message.blocks)
                    if let usage = message.usage { current.usage = usage }
                    current.lastSourceSortOrder = index
                } else {
                    if raw.role == .user { currentAssistant = nil }
                    if !message.content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                        || !message.blocks.isEmpty || !message.attachments.isEmpty {
                        message.sourceSortOrder = index
                        message.lastSourceSortOrder = index
                        uiMessages.append(message)
                        if raw.role == .assistant { currentAssistant = message }
                    }
                }
            }
            var textBlocks = 0
            for message in uiMessages where message.role == .assistant {
                for block in message.blocks {
                    if case .text = block.kind, !block.content.isEmpty {
                        vm.cacheAttributedString(for: block)
                        textBlocks += 1
                    }
                    // Clean export omits tool-result rows. Match loadSession's
                    // orphan finalization; do not fabricate successful outputs.
                    if let status = block.toolStatus {
                        switch status {
                        case .running, .streaming: block.toolStatus = .cancelled
                        case .success, .failed, .cancelled: break
                        }
                    }
                }
            }
            vm.messages = uiMessages
            guard textBlocks > 0 else { throw failure("没有可回放的助手文本") }
            let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
            let replay = NativeReplayController(vm: vm, inputSHA256: digest, textBlocks: textBlocks,
                rawEntries: entries.count, omittedMedia: omittedMedia)
            navigationController?.pushViewController(replay, animated: false)
        } catch {
            status.text = "导入失败：\(error.localizedDescription)\n未开始采集，也未修改正常 Minis。"
        }
    }

    private func failure(_ text: String) -> NSError {
        NSError(domain: "NativeReplayInput", code: 1, userInfo: [NSLocalizedDescriptionKey: text])
    }
}

@MainActor
private final class NativeReplayController: UIViewController {
    private let vm: AIChatViewModel
    private let inputSHA256: String
    private let textBlocks: Int
    private let rawEntries: Int
    private let omittedMedia: Int
    private var listHost: UIHostingController<NativeReplayList>?
    private var captureActive = false
    private var captureFinished = false
    private var startedAt: TimeInterval?
    private var snapshots: [[String: Any]] = []
    private var gestureBegins = 0
    private var gestureEnds = 0
    private var reentryCount = 0
    private var reentryPending = false
    private var observedPan: UIPanGestureRecognizer?
    private var captureTimeout: Task<Void, Never>?
    private var reportURL: URL?
    private lazy var captureButton = UIBarButtonItem(title: "开始采集", style: .plain, target: self, action: #selector(captureAction))
    private lazy var reentryButton = UIBarButtonItem(title: "重入", style: .plain, target: self, action: #selector(reenter))

    init(vm: AIChatViewModel, inputSHA256: String, textBlocks: Int, rawEntries: Int, omittedMedia: Int) {
        self.vm = vm
        self.inputSHA256 = inputSHA256
        self.textBlocks = textBlocks
        self.rawEntries = rawEntries
        self.omittedMedia = omittedMedia
        super.init(nibName: nil, bundle: nil)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) is not used") }

    override func viewDidLoad() {
        super.viewDidLoad()
        title = "先浏览，准备后开始采集"
        view.backgroundColor = .systemBackground
        navigationItem.hidesBackButton = true
        navigationItem.rightBarButtonItems = [captureButton, reentryButton]
        reentryButton.isEnabled = false
        mountList()
        NotificationCenter.default.addObserver(self, selector: #selector(leftForeground),
            name: UIApplication.willResignActiveNotification, object: nil)
    }

    private func mountList() {
        let host = UIHostingController(rootView: NativeReplayList(vm: vm))
        host.view.backgroundColor = .clear
        host.view.translatesAutoresizingMaskIntoConstraints = false
        addChild(host)
        view.addSubview(host.view)
        NSLayoutConstraint.activate([
            host.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            host.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            host.view.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            host.view.bottomAnchor.constraint(equalTo: view.safeAreaLayoutGuide.bottomAnchor)
        ])
        host.didMove(toParent: self)
        listHost = host
    }

    private func first<T: UIView>(_ type: T.Type, in view: UIView) -> T? {
        if let value = view as? T { return value }
        for child in view.subviews { if let value = first(type, in: child) { return value } }
        return nil
    }

    private var collection: UICollectionView? {
        guard let root = listHost?.view else { return nil }
        return first(UICollectionView.self, in: root)
    }

    private func bindPanObservation() {
        observedPan?.removeTarget(self, action: #selector(panObserved))
        observedPan = collection?.panGestureRecognizer
        observedPan?.addTarget(self, action: #selector(panObserved))
    }

    private func marker(_ phase: String, values: [String: Double] = [:]) {
        ReentryDiagnostics.shared.record(kind: "replay-marker", owner: collection,
            subject: self, phase: phase, values: values)
    }

    private func rect(_ r: CGRect) -> [Double] {
        [Double(r.minX), Double(r.minY), Double(r.width), Double(r.height)]
    }

    // Length-prefixed model fields, including IDs and block boundaries. This
    // reads only in-memory content; the report contains the digest, not text.
    private func modelDigest() -> String {
        var hash = SHA256()
        func field(_ value: String) {
            let bytes = Data(value.utf8)
            hash.update(data: Data("\(bytes.count):".utf8))
            hash.update(data: bytes)
        }
        field(String(vm.messages.count))
        for message in vm.messages {
            field(message.id.uuidString)
            field(message.content)
            field(String(message.blocks.count))
            for block in message.blocks {
                field(block.id.uuidString)
                field(block.content)
            }
        }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }

    // Passive state only: never asks a subject for intrinsic/sizeThatFits/layout.
    private func snapshot(_ phase: String) {
        guard let cv = collection, let layout = cv.collectionViewLayout as? MessageListLayout else { return }
        let rows: [[String: Any]] = cv.indexPathsForVisibleItems.sorted().compactMap { path in
            guard let cell = cv.cellForItem(at: path) as? SelfSizingCell else { return nil }
            var result: [String: Any] = [
                "index": path.item, "cellID": ReentryDiagnostics.identity(cell),
                "generation": cell.reentryDiagnosticGeneration, "frame": rect(cell.frame),
                "inWindow": cell.window != nil && !cell.isHidden && cell.alpha > 0,
                "cache": layout.cachedHeight(at: path.item).map { Double($0) } as Any? ?? NSNull()
            ]
            if let text = first(SelectableMarkdownTextView.self, in: cell) {
                result["inWindow"] = cell.window != nil && !cell.isHidden && cell.alpha > 0
                    && text.window != nil && !text.isHidden && text.alpha > 0
                result["textID"] = ReentryDiagnostics.identity(text)
                result["textBounds"] = rect(text.bounds)
                result["textLength"] = text.textStorage.length
                result["markdownSHA256"] = SHA256.hash(data: Data(text.rawMarkdown.utf8)).map { String(format: "%02x", $0) }.joined()
            }
            return result
        }
        snapshots.append([
            "phase": phase, "t": ProcessInfo.processInfo.systemUptime,
            "collectionID": ReentryDiagnostics.identity(cv), "viewport": rect(cv.bounds),
            "vmID": ReentryDiagnostics.identity(vm), "modelSHA256": modelDigest(),
            "productionCoordinator": cv.delegate is CollectionViewMessageListV3.Coordinator,
            "items": cv.numberOfSections > 0 ? cv.numberOfItems(inSection: 0) : 0,
            "contentHeight": Double(cv.contentSize.height), "offset": Double(cv.contentOffset.y),
            "tracking": cv.isTracking, "decelerating": cv.isDecelerating,
            "deferred": layout.deferSelfSizing, "visibleRows": rows
        ])
    }

    @objc private func captureAction() {
        if reportURL != nil { shareReport(); return }
        if captureActive { finishCapture(reason: "user-finished"); return }
        guard !captureFinished, #unavailable(iOS 16.0),
              UIApplication.shared.applicationState == .active,
              let cv = collection, cv.window != nil, cv.numberOfSections > 0,
              cv.numberOfItems(inSection: 0) > 0, !cv.visibleCells.isEmpty,
              cv.bounds.width > 1, cv.bounds.height > 1, !cv.isTracking, !cv.isDragging,
              cv.delegate is CollectionViewMessageListV3.Coordinator else {
            title = "INVALID：真实列表尚未就绪"
            return
        }
        do { try ReentryDiagnostics.shared.nativeProbeBeginCapture() }
        catch { title = "请重开测试 App，开始一次新采集"; return }
        captureActive = true
        startedAt = ProcessInfo.processInfo.systemUptime
        bindPanObservation()
        marker("begin", values: ["messages": Double(vm.messages.count), "textBlocks": Double(textBlocks)])
        snapshot("begin")
        title = "采集中：上下滑动，完成后分享"
        captureButton.title = "结束分享"
        reentryButton.isEnabled = true
        // One bounded manual window. The collector's unchanged8000-event cap
        // still applies; expiry is reported, never treated as a successful test.
        captureTimeout = Task { [weak self] in
            do { try await Task.sleep(nanoseconds: 20_000_000_000) } catch { return }
            guard !Task.isCancelled else { return }
            self?.finishCapture(reason: "time-limit")
        }
    }

    @objc private func panObserved(_ pan: UIPanGestureRecognizer) {
        guard captureActive else { return }
        if pan.state == .began {
            gestureBegins += 1
            marker("finger-began", values: [
                "state": Double(pan.state.rawValue),
                "gesture": Double(gestureBegins)
            ])
        } else if pan.state == .ended || pan.state == .cancelled {
            gestureEnds += 1
            marker("finger-ended", values: [
                "state": Double(pan.state.rawValue),
                "gesture": Double(gestureEnds)
            ])
        }
    }

    @objc private func reenter() {
        guard captureActive, !reentryPending, let old = listHost,
              let cv = collection, !cv.isTracking, !cv.isDragging else { return }
        reentryPending = true
        reentryButton.isEnabled = false
        reentryCount += 1
        let count = reentryCount
        marker("leave", values: ["count": Double(count)])
        snapshot("before-leave")
        observedPan?.removeTarget(self, action: #selector(panObserved))
        observedPan = nil
        old.willMove(toParent: nil)
        old.view.removeFromSuperview()
        old.removeFromParent()
        listHost = nil
        // A fresh real Representable/Coordinator with the same VM, messages and
        // caches. This is view unmount/remount, not normal-App navigation. No
        // synthetic transition flags, datasource, heights or drag callbacks.
        mountList()
        Task { [weak self] in
            guard let self else { return }
            for _ in 0..<30 {
                guard self.captureActive else { return }
                if let next = self.collection, next.window != nil,
                   next.delegate is CollectionViewMessageListV3.Coordinator,
                   next.numberOfSections > 0, next.numberOfItems(inSection: 0) > 0,
                   !next.visibleCells.isEmpty, next.bounds.width > 1, next.bounds.height > 1 {
                    self.reentryPending = false
                    self.bindPanObservation()
                    self.marker("reentered", values: ["count": Double(count)])
                    self.snapshot("after-reentry")
                    self.reentryButton.isEnabled = true
                    return
                }
                // Wait for UIKit to mount naturally, never force subject layout
                // or wait for a height to recover. Trace stays active throughout.
                do { try await NativeFrameWaiter().wait(1) }
                catch { self.finishCapture(reason: "reentry-unready"); return }
            }
            self.finishCapture(reason: "reentry-unready")
        }
    }

    @objc private func leftForeground() {
        if captureActive { finishCapture(reason: "left-foreground") }
    }

    private func finishCapture(reason: String) {
        guard captureActive else { return }
        let finishReason = reentryPending && reason == "user-finished" ? "reentry-pending" : reason
        marker("end", values: ["gestureBegins": Double(gestureBegins), "gestureEnds": Double(gestureEnds)])
        snapshot("end")
        captureActive = false
        captureFinished = true
        captureTimeout?.cancel()
        captureTimeout = nil
        observedPan?.removeTarget(self, action: #selector(panObserved))
        observedPan = nil
        ReentryDiagnostics.shared.nativeProbePauseCapture()
        reentryButton.isEnabled = false
        do {
            let trace = try ReentryDiagnostics.shared.nativeProbeReadEvents()
            let commit = (Bundle.main.object(forInfoDictionaryKey: "MinisDiagnosticCommit") as? String) ?? "unknown"
            let report: [String: Any] = [
                "schema": 1, "kind": "minis-production-list-replay",
                "sourceCommit": commit, "baselineCommit": "2f21e242df71d63576682f8ac61c50a097f3a5ae",
                "runID": ReentryDiagnostics.shared.nativeProbeRunID, "os": UIDevice.current.systemVersion,
                "inputSHA256": inputSHA256, "messageCount": vm.messages.count,
                "rawEntryCount": rawEntries, "omittedMediaCount": omittedMedia,
                "textBlockCount": textBlocks, "gestureBegins": gestureBegins, "gestureEnds": gestureEnds,
                "reentries": reentryCount, "finishReason": finishReason,
                "startedAt": startedAt ?? 0, "snapshots": snapshots, "trace": trace,
                "limits": "Actual production V3 coordinator, RawMessage.toChatMessage and completed caches; local merged export. Export omits media bytes and tool results (unresolved tools finalized cancelled). Reasoning enabled; bar inputs debug60/50. No normal Minis storage. Capture only, not a repair verdict."
            ]
            let folder = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("real-list-replay", isDirectory: true)
                .appendingPathComponent(ReentryDiagnostics.shared.nativeProbeRunID, isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let url = folder.appendingPathComponent("report.json")
            try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]).write(to: url, options: .atomic)
            reportURL = url
            captureButton.title = "分享报告"
            let capped = trace.contains { ($0["kind"] as? String) == "limit" }
            title = capped ? "达到记录上限，仍可分享" : "采集结束，请分享报告"
            if reason == "user-finished" { shareReport() }
        } catch { title = "INVALID：报告写出失败" }
    }

    private func shareReport() {
        guard let reportURL else { return }
        let share = UIActivityViewController(activityItems: [reportURL], applicationActivities: nil)
        share.popoverPresentationController?.barButtonItem = captureButton
        present(share, animated: true)
    }
}

@main
@MainActor
final class NativeProvisionalHeightApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        ReentryDiagnostics.shared.nativeProbePauseCapture()
        window.rootViewController = UINavigationController(rootViewController: NativeReplayImportController())
        self.window = window
        window.makeKeyAndVisible()
        return true
    }
}
#endif
