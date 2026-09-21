import SwiftUI
import UIKit

// Diagnostic-only probe. It exercises the legacy hosting bridge with the
// production CodeBlockAttachment measurement path. It does not compile the
// full Markdown renderer and contains no private conversation text.

struct SelectableMarkdownTheme {
    let baseFontSize: CGFloat
    let codeBlockCornerRadius: CGFloat = 8

    init(baseFontSize: CGFloat = 16.5) {
        self.baseFontSize = baseFontSize
    }

    var codeBlockBackground: UIColor {
        UIColor { traits in
            traits.userInterfaceStyle == .dark ? UIColor(white: 0.15, alpha: 1) : .black
        }
    }

    var codeBlockTextColor: UIColor {
        UIColor { traits in
            traits.userInterfaceStyle == .dark
                ? UIColor(red: 0.55, green: 0.95, blue: 0.55, alpha: 1)
                : .systemGreen
        }
    }

    var codeBlockFont: UIFont {
        let size = baseFontSize * 0.85
        if let menlo = UIFont(name: "Menlo", size: size) {
            let descriptor = menlo.fontDescriptor.addingAttributes([
                .cascadeList: [UIFontDescriptor(fontAttributes: [.name: "PingFang SC"])]
            ])
            return UIFont(descriptor: descriptor, size: size)
        }
        return .monospacedSystemFont(ofSize: size, weight: .regular)
    }
}

enum WidthContract: String, CaseIterable {
    case currentFallback
    case noWidthDemandFallback
}

@MainActor
final class ProbeModel: ObservableObject {
    @Published var text = NSAttributedString(string: "")
    @Published var viewport: CGFloat = 428
    let contract: WidthContract

    init(contract: WidthContract) {
        self.contract = contract
    }
}

final class ProbeTextView: UITextView {
    let contract: WidthContract

    init(contract: WidthContract) {
        self.contract = contract
        let storage = NSTextStorage()
        let manager = NSLayoutManager()
        let container = NSTextContainer()
        container.lineFragmentPadding = 0
        container.widthTracksTextView = true
        manager.addTextContainer(container)
        storage.addLayoutManager(manager)
        super.init(frame: .zero, textContainer: container)
        isEditable = false
        isSelectable = true
        isScrollEnabled = false
        textContainerInset = UIEdgeInsets(top: 4, left: 0, bottom: 4, right: 0)
        textContainer.lineBreakMode = .byWordWrapping
        contentInset = .zero
        backgroundColor = .clear
        clipsToBounds = true
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override var intrinsicContentSize: CGSize {
        let original = super.intrinsicContentSize
        if contract == .noWidthDemandFallback {
            guard bounds.width > 1 && bounds.width.isFinite else {
                // Candidate: do not let an unestablished UITextView bounds
                // turn its ideal width into a SwiftUI width demand.
                return CGSize(width: UIView.noIntrinsicMetric, height: original.height)
            }
            let height = sizeThatFits(CGSize(width: bounds.width,
                                               height: .greatestFiniteMagnitude)).height
            return CGSize(width: UIView.noIntrinsicMetric, height: height)
        }

        guard bounds.width > 1 && bounds.width.isFinite else {
            // Current production fallback: when bounds.width is not
            // established, it returns `original`, retaining UITextView's
            // ideal width demand.
            return original
        }
        let height = sizeThatFits(CGSize(width: bounds.width,
                                           height: .greatestFiniteMagnitude)).height
        return CGSize(width: UIView.noIntrinsicMetric, height: height)
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        if textContainer.size.height < CGFloat.greatestFiniteMagnitude {
            textContainer.size.height = .greatestFiniteMagnitude
        }
    }
}

struct ProbeRepresentable: UIViewRepresentable {
    @ObservedObject var model: ProbeModel

    func makeUIView(context: Context) -> ProbeTextView {
        let view = ProbeTextView(contract: model.contract)
        view.setContentHuggingPriority(.required, for: .vertical)
        view.setContentCompressionResistancePriority(.required, for: .vertical)
        return view
    }

    func updateUIView(_ view: ProbeTextView, context: Context) {
        if view.attributedText != model.text {
            view.textStorage.setAttributedString(model.text)
            view.invalidateIntrinsicContentSize()
            view.setNeedsLayout()
        }
    }
}

struct ProbeContent: View {
    @ObservedObject var model: ProbeModel

    var body: some View {
        ProbeRepresentable(model: model)
            .fixedSize(horizontal: false, vertical: true)
            .padding(.vertical, 2)
            .frame(maxWidth: model.viewport, alignment: .leading)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
    }
}

struct ReportEvent: Codable {
    let contract: String
    let phase: String
    let width: Double
    let height: Double
    let elapsedMS: Double
}

struct GeometrySample: Codable {
    let contract: String
    let phase: String
    let step: String
    let viewportWidth: Double
    let expectedTextWidth: Double
    let hostWidth: Double
    let textWidth: Double
    let textHeight: Double
    let containerWidth: Double
    let reportedWidth: Double
    let reportedHeight: Double
    let eventCount: Int
    let bounded: Bool
}

struct ProbeReport: Codable {
    let kind: String
    let runID: String
    let os: String
    let samples: [GeometrySample]
    let events: [ReportEvent]
    let currentFallbackReproducesOverflow: Bool
    let noWidthDemandFallbackBounded: Bool
    let verdict: String
    let limits: [String]
}

@MainActor
final class ProbeRunner {
    let root: UIViewController
    let output: URL
    var contractIndex = 0
    var phaseIndex = 0
    var stepIndex = 0
    var model: ProbeModel?
    var container: UIView?
    var contentView: UIView?
    var lastReport = CGSize.zero
    var events: [ReportEvent] = []
    var samples: [GeometrySample] = []
    let startTime = CACurrentMediaTime()
    let phases = ["cold", "long", "shrink", "restore"]
    let steps: [(name: String, delay: TimeInterval)] = [
        ("early", 0.12), ("settled", 0.42), ("late", 0.78)
    ]

    init(root: UIViewController) {
        self.root = root
        output = URL(fileURLWithPath: NSHomeDirectory())
            .appendingPathComponent("Documents/session-width-probe", isDirectory: true)
        try? FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
    }

    var contract: WidthContract { WidthContract.allCases[contractIndex] }

    func startNextContract() {
        contentView?.removeFromSuperview()
        container?.removeFromSuperview()
        contentView = nil
        container = nil
        guard contractIndex < WidthContract.allCases.count else {
            finish()
            return
        }

        let state = ProbeModel(contract: contract)
        model = state
        let box = UIView(frame: CGRect(x: 0, y: 20, width: 428, height: 820))
        box.clipsToBounds = true
        box.backgroundColor = .systemBackground
        root.view.addSubview(box)
        container = box

        let configuration = LegacyHostingConfiguration(
            content: AnyView(ProbeContent(model: state)),
            parent: WeakHostingParent(root),
            onSizeChange: { [weak self] size in
                guard let self else { return }
                self.lastReport = size
                self.events.append(ReportEvent(
                    contract: self.contract.rawValue,
                    phase: self.phases[self.phaseIndex],
                    width: Double(size.width),
                    height: Double(size.height),
                    elapsedMS: (CACurrentMediaTime() - self.startTime) * 1000))
            })
        let hosted = configuration.makeContentView()
        box.addSubview(hosted)
        hosted.frame = box.bounds
        hosted.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        contentView = hosted
        phaseIndex = 0
        runPhase()
    }

    func fixture(phase: String) -> NSAttributedString {
        let theme = SelectableMarkdownTheme()
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineSpacing = 2
        let textAttributes: [NSAttributedString.Key: Any] = [
            .font: theme.baseFontSize > 0 ? UIFont.systemFont(ofSize: theme.baseFontSize) : UIFont.systemFont(ofSize: 16.5),
            .foregroundColor: UIColor.label,
            .paragraphStyle: paragraph,
        ]
        let result = NSMutableAttributedString()
        result.append(NSAttributedString(
            string: "确认绑定了。这里是一段需要在消息气泡宽度内稳定排版的说明文字。\n\n",
            attributes: textAttributes))

        let blocks: [(String, String?)] = [
            ("let enabled = true\nlet account = \"official\"\n", "swift"),
            ("-[ViewController toggleFeature:]\nif (sender.isOn) {\n    applyFeature();\n}\n", "objc"),
            ("function checkState(value) {\n  return value === true;\n}\n", "javascript"),
            ("autoCheckAndFollowOfficialAccount\n    -> _autoFollowOfficialAccountsWithControl:\n", "text"),
            ("final class ConfigRecord {\n    let key: String\n    let value: Bool\n}\n", "swift"),
        ]
        let count = phase == "shrink" ? 2 : blocks.count
        for index in 0..<count {
            let block = blocks[index]
            let attachment = CodeBlockAttachment(
                code: block.0,
                language: block.1,
                theme: theme,
                quoteDepth: 0,
                contentFingerprint: block.0.hashValue)
            result.append(NSAttributedString(attachment: attachment))
            result.append(NSAttributedString(string: "\n\n", attributes: textAttributes))
        }
        if phase != "cold" {
            result.append(NSAttributedString(
                string: String(repeating: "当前开关状态和实际调用链需要保持一致。\n", count: phase == "long" ? 4 : 2),
                attributes: textAttributes))
        }
        return result
    }

    func runPhase() {
        guard let model, let box = container else { return }
        let phase = phases[phaseIndex]
        let viewport: CGFloat = phase == "shrink" ? 320 : 428
        model.viewport = viewport
        box.frame.size.width = viewport
        model.text = fixture(phase: phase)
        stepIndex = 0
        captureNextStep()
    }

    func captureNextStep() {
        guard stepIndex < steps.count else {
            phaseIndex += 1
            if phaseIndex < phases.count {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.12) { [self] in runPhase() }
            } else {
                contractIndex += 1
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [self] in startNextContract() }
            }
            return
        }
        let step = steps[stepIndex]
        DispatchQueue.main.asyncAfter(deadline: .now() + step.delay) { [self] in
            capture(step: step.name)
            stepIndex += 1
            captureNextStep()
        }
    }

    func textViews(in view: UIView) -> [ProbeTextView] {
        if let view = view as? ProbeTextView { return [view] }
        return view.subviews.flatMap { textViews(in: $0) }
    }

    func referenceHeight(_ text: NSAttributedString, width: CGFloat) -> CGFloat {
        let storage = NSTextStorage(attributedString: text)
        let manager = NSLayoutManager()
        let container = NSTextContainer(size: CGSize(width: width, height: 10_000_000))
        container.lineFragmentPadding = 0
        manager.addTextContainer(container)
        storage.addLayoutManager(manager)
        manager.ensureLayout(for: container)
        return ceil(manager.usedRect(for: container).maxY + 8)
    }

    func capture(step: String) {
        guard let model, let box = container else { return }
        let viewport = model.viewport
        let expectedWidth = viewport - 32
        let views = textViews(in: box)
        guard let view = views.first else {
            samples.append(GeometrySample(
                contract: contract.rawValue, phase: phases[phaseIndex], step: step,
                viewportWidth: Double(viewport), expectedTextWidth: Double(expectedWidth),
                hostWidth: Double(contentView?.bounds.width ?? -1), textWidth: -1,
                textHeight: -1, containerWidth: -1,
                reportedWidth: Double(lastReport.width), reportedHeight: Double(lastReport.height),
                eventCount: events.count, bounded: false))
            return
        }
        let rect = view.convert(view.bounds, to: box)
        let width = view.bounds.width
        let bounded = width.isFinite && width > 1 && width <= expectedWidth + 1
            && rect.minX >= 15 && rect.maxX <= viewport - 15
        samples.append(GeometrySample(
            contract: contract.rawValue, phase: phases[phaseIndex], step: step,
            viewportWidth: Double(viewport), expectedTextWidth: Double(expectedWidth),
            hostWidth: Double(contentView?.bounds.width ?? -1), textWidth: Double(width),
            textHeight: Double(view.bounds.height), containerWidth: Double(view.textContainer.size.width),
            reportedWidth: Double(lastReport.width), reportedHeight: Double(lastReport.height),
            eventCount: events.count, bounded: bounded))
        print("[SessionWidthProbe] \(contract.rawValue) \(phases[phaseIndex]) \(step) width=\(width) tc=\(view.textContainer.size.width) host=\(contentView?.bounds.width ?? -1) report=\(lastReport)")
    }

    func finish() {
        let current = samples.filter { $0.contract == WidthContract.currentFallback.rawValue }
        let candidate = samples.filter { $0.contract == WidthContract.noWidthDemandFallback.rawValue }
        let currentOverflow = current.contains { $0.textWidth > $0.expectedTextWidth + 1 || $0.containerWidth > $0.expectedTextWidth + 1 }
        let candidateBounded = !candidate.isEmpty && candidate.allSatisfy { $0.bounded }
        let verdict: String
        if !currentOverflow {
            verdict = "inconclusive-current-fallback-did-not-reproduce-overflow"
        } else if candidateBounded {
            verdict = "candidate-suppresses-invalid-width-demand"
        } else {
            verdict = "candidate-did-not-suppress-overflow"
        }
        let report = ProbeReport(
            kind: "legacy-hosting-session-width-contract-probe",
            runID: ProcessInfo.processInfo.environment["PROBE_RUN_ID"] ?? "missing",
            os: UIDevice.current.systemVersion,
            samples: samples,
            events: events,
            currentFallbackReproducesOverflow: currentOverflow,
            noWidthDemandFallbackBounded: candidateBounded,
            verdict: verdict,
            limits: [
                "Diagnostic component/API-path probe, not the full OpenMinis app.",
                "Uses production LegacyHostingContent and byte-extracted production CodeBlockAttachment.",
                "Uses a neutral five-code-block fixture, not private conversation text.",
                "Runs pinned iOS26.2 simulator; it is not an iOS15 runtime or device acceptance test.",
            ])
        do {
            let data = try JSONEncoder.prettySorted.encode(report)
            try data.write(to: output.appendingPathComponent("report.json"), options: .atomic)
            print("[SessionWidthProbe] COMPLETE verdict=\(verdict) samples=\(samples.count)")
            exit(verdict == "candidate-suppresses-invalid-width-demand" ? 0 : 1)
        } catch {
            print("[SessionWidthProbe] REPORT-ERROR \(error)")
            exit(3)
        }
    }
}

private extension JSONEncoder {
    static var prettySorted: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }
}

@main
final class SessionWidthProbeApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    var runner: ProbeRunner?

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        NSTextContainerSetSizeGuard.install()
        let window = UIWindow(frame: UIScreen.main.bounds)
        let controller = UIViewController()
        controller.view.backgroundColor = .systemBackground
        window.rootViewController = controller
        self.window = window
        window.makeKeyAndVisible()
        runner = ProbeRunner(root: controller)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.runner?.startNextContract()
        }
        return true
    }
}
