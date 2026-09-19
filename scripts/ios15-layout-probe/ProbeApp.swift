// Minimal native sizing probe, not the complete app or an iOS15 runtime.
// LegacyHostingContent, ThematicBreakAttachment and the TextKit guard are
// compiled from production sources by run_ios15_layout_probe.sh.
import SwiftUI
import UIKit

// The extracted attachment only reads this color when painting its rule.
// All of its sizing implementation is the unmodified production class.
struct SelectableMarkdownTheme {
    let secondaryLabelColor = UIColor.secondaryLabel
}

enum ProbeMode: String, CaseIterable {
    case baseline, intrinsicAtBoundsWidth, lowHorizontalResistance, noIntrinsicWidth, explicitFrame, directHost
}

@MainActor
final class ProbeModel: ObservableObject {
    @Published var text = NSAttributedString(string: "")
    @Published var viewport: CGFloat = 428
    let mode: ProbeMode
    init(mode: ProbeMode) { self.mode = mode }
}

final class ProbeTextView: UITextView {
    var mode: ProbeMode = .baseline

    init() {
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
        if mode == .noIntrinsicWidth {
            return CGSize(width: UIView.noIntrinsicMetric, height: original.height)
        }
        // Candidate fix shape: the fallback bridge measures the ideal height
        // at an UNBOUNDED width (the iOS15 path has no representable
        // sizeThatFits), so the intrinsic height comes out as if the text
        // were laid out at 1e7pt. Report no width demand and measure the
        // height at the CURRENT live width instead. This is plain UIKit and
        // therefore works on the iOS 15 runtime, unlike the iOS16-only
        // UIViewRepresentable.sizeThatFits override.
        if mode == .intrinsicAtBoundsWidth {
            let width = bounds.width
            if width > 1 && width.isFinite {
                let height = sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude)).height
                return CGSize(width: UIView.noIntrinsicMetric, height: height)
            }
            return CGSize(width: UIView.noIntrinsicMetric, height: original.height)
        }
        return original
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        // Same live TextKit height policy as SelectableMarkdownTextView.
        if textContainer.size.height < CGFloat.greatestFiniteMagnitude {
            textContainer.size.height = .greatestFiniteMagnitude
        }
    }
}

// Intentionally NO iOS16 sizeThatFits callback: exercise fallback bridging.
struct ProbeRepresentable: UIViewRepresentable {
    @ObservedObject var model: ProbeModel
    func makeUIView(context: Context) -> ProbeTextView {
        let view = ProbeTextView()
        view.mode = model.mode
        view.setContentHuggingPriority(.required, for: .vertical)
        view.setContentCompressionResistancePriority(.required, for: .vertical)
        if model.mode == .lowHorizontalResistance {
            view.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
        }
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
        Group {
            if model.mode == .explicitFrame {
                ProbeRepresentable(model: model)
                    .frame(width: model.viewport - 32, alignment: .leading)
            } else {
                ProbeRepresentable(model: model)
            }
        }
        // Match AssistantBlockView + BridgedAssistantBlockV3 modifiers.
        .fixedSize(horizontal: false, vertical: true)
        .padding(.vertical, 2)
        .frame(maxWidth: model.viewport, alignment: .leading)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 16)
    }
}

@MainActor
final class ProbeRunner {
    let root: UIViewController
    let output = URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Documents/layout-probe")
    var modeIndex = 0
    var phaseIndex = 0
    var model: ProbeModel?
    var container: UIView?
    var contentView: UIView?
    var directController: UIViewController?
    var lastReport = CGSize.zero
    var reportCount = 0
    var samples: [[String: Any]] = []
    let phases = ["short", "long", "rule", "growth", "narrow", "shrink", "restore"]
    let caption = UILabel()

    init(root: UIViewController) {
        self.root = root
        try! FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
        caption.font = .monospacedSystemFont(ofSize: 11, weight: .regular)
        caption.frame = CGRect(x: 8, y: 30, width: 410, height: 30)
        root.view.addSubview(caption)
    }

    func startNextMode() {
        contentView?.removeFromSuperview()
        container?.removeFromSuperview()
        if let controller = directController {
            controller.willMove(toParent: nil)
            controller.removeFromParent()
        }
        directController = nil
        contentView = nil
        container = nil
        guard modeIndex < ProbeMode.allCases.count else { finish(); return }
        let mode = ProbeMode.allCases[modeIndex]
        let state = ProbeModel(mode: mode)
        model = state
        lastReport = .zero
        reportCount = 0
        let box = UIView(frame: CGRect(x: 0, y: 70, width: 428, height: 801))
        box.clipsToBounds = true
        box.layer.borderColor = UIColor.red.cgColor
        box.layer.borderWidth = 1
        root.view.addSubview(box)
        container = box
        let hosted: UIView
        if mode == .directHost {
            let controller = UIHostingController(rootView: ProbeContent(model: state)
                .fixedSize(horizontal: false, vertical: true))
            root.addChild(controller)
            hosted = controller.view
            box.addSubview(hosted)
            controller.didMove(toParent: root)
            directController = controller
        } else {
            let config = LegacyHostingConfiguration(
                content: AnyView(ProbeContent(model: state)),
                parent: WeakHostingParent(root),
                onSizeChange: { [weak self] size in
                    self?.lastReport = size
                    self?.reportCount += 1
                })
            hosted = config.makeContentView()
            box.addSubview(hosted)
        }
        hosted.frame = box.bounds
        hosted.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        contentView = hosted
        phaseIndex = 0
        runPhase()
    }

    func attributed(phase: String) -> NSAttributedString {
        let paragraph = NSMutableParagraphStyle()
        paragraph.lineSpacing = 2
        let attributes: [NSAttributedString.Key: Any] = [
            .font: UIFont.systemFont(ofSize: 16.5), .foregroundColor: UIColor.label,
            .paragraphStyle: paragraph,
        ]
        if phase == "short" || phase == "shrink" {
            return NSAttributedString(string: "Hello. 短句。", attributes: attributes)
        }
        let sentence = "A bounded paragraph must wrap inside its parent. 中文也应正常换行，不改变页面宽度。\n"
        let result = NSMutableAttributedString(string: String(repeating: sentence, count: 3), attributes: attributes)
        if phase != "long" {
            result.append(NSAttributedString(attachment: ThematicBreakAttachment(theme: SelectableMarkdownTheme())))
            result.append(NSAttributedString(string: "\nTrailing paragraph below the full-width rule. 分隔线后的文字。\n", attributes: attributes))
        }
        if ["growth", "narrow", "restore"].contains(phase) {
            result.append(NSAttributedString(string: String(repeating: sentence, count: 3), attributes: attributes))
        }
        return result
    }

    func runPhase() {
        guard let model, let box = container else { return }
        let phase = phases[phaseIndex]
        model.viewport = ["narrow", "shrink"].contains(phase) ? 320 : 428
        box.frame.size.width = model.viewport
        model.text = attributed(phase: phase)
        caption.text = "\(model.mode.rawValue) / \(phase) / \(Int(model.viewport))pt"
        // Let ordinary UIKit/SwiftUI runloop layout converge; no manual
        // systemLayoutSizeFitting or intrinsic query drives the live view.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.65) { [self] in
            capture()
            phaseIndex += 1
            if phaseIndex < phases.count { runPhase() }
            else {
                modeIndex += 1
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { self.startNextMode() }
            }
        }
    }

    func textViews(in view: UIView) -> [ProbeTextView] {
        (view as? ProbeTextView).map { [$0] } ?? view.subviews.flatMap { textViews(in: $0) }
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

    func capture() {
        guard let model, let box = container else { return }
        let phase = phases[phaseIndex]
        let expected = referenceHeight(model.text, width: model.viewport - 32)
        let views = textViews(in: box)
        var sample: [String: Any] = [
            "mode": model.mode.rawValue, "phase": phase, "viewport_width": model.viewport,
            "host_width": contentView?.bounds.width ?? -1, "expected_text_width": model.viewport - 32,
            "expected_text_height": expected, "text_view_count": views.count,
            "last_report_width": lastReport.width, "last_report_height": lastReport.height,
            "report_count": reportCount,
        ]
        if let view = views.first {
            let rect = view.convert(view.bounds, to: box)
            let bounded = view.bounds.width.isFinite && view.bounds.width > 1
                && view.bounds.width <= model.viewport - 32 + 1
                && rect.minX >= 15 && rect.maxX <= model.viewport - 15
            let heightOK = view.bounds.height.isFinite && abs(view.bounds.height - expected) <= 4
            sample.merge([
                "text_width": view.bounds.width, "text_height": view.bounds.height,
                "container_width": view.textContainer.size.width,
                "text_x": rect.minX, "text_right": rect.maxX,
                "attached": view.window != nil, "bounded": bounded,
                "height_ok": heightOK, "pass": bounded && heightOK && view.window != nil,
            ]) { _, new in new }
        } else {
            sample["pass"] = false
            sample["bounded"] = false
            sample["height_ok"] = false
        }
        samples.append(sample)
        if phase == "long" || phase == "rule" || phase == "narrow" {
            let renderer = UIGraphicsImageRenderer(bounds: box.bounds)
            let image = renderer.image { _ in box.drawHierarchy(in: box.bounds, afterScreenUpdates: false) }
            try? image.pngData()?.write(to: output.appendingPathComponent("\(model.mode.rawValue)-\(phase).png"))
        }
        print("[LayoutProbe] \(model.mode.rawValue) \(phase) \(sample)")
    }

    func finish() {
        let baseline = samples.filter { ($0["mode"] as? String) == ProbeMode.baseline.rawValue }
        let reproduced = baseline.contains { ($0["text_width"] as? CGFloat ?? 0) > ($0["expected_text_width"] as? CGFloat ?? .greatestFiniteMagnitude) + 1 }
        let passingModes = ProbeMode.allCases.filter { mode in
            let group = samples.filter { ($0["mode"] as? String) == mode.rawValue }
            return group.count == phases.count && group.allSatisfy { ($0["pass"] as? Bool) == true }
        }.map(\.rawValue)
        let report: [String: Any] = [
            "kind": "minimal-native-api-probe-not-full-app", "os": UIDevice.current.systemVersion,
            "baseline_reproduces_live_width_overflow": reproduced,
            "passing_modes": passingModes, "samples": samples,
        ]
        do {
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: output.appendingPathComponent("report.json"))
            print("[LayoutProbe] COMPLETE baseline_reproduces=\(reproduced) passing=\(passingModes)")
            fflush(stdout)
            exit(0)
        } catch {
            print("[LayoutProbe] REPORT-ERROR \(error)")
            exit(3)
        }
    }
}

@main
final class ProbeAppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    var runner: ProbeRunner?
    func application(_ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        NSTextContainerSetSizeGuard.install()
        let window = UIWindow(frame: UIScreen.main.bounds)
        let controller = UIViewController()
        controller.view.backgroundColor = .white
        window.rootViewController = controller
        self.window = window
        window.makeKeyAndVisible()
        runner = ProbeRunner(root: controller)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { self.runner?.startNextMode() }
        return true
    }
}
