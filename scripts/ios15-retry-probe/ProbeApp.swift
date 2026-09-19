import UIKit
import SwiftUI

// Purpose: reproduce on the pinned simulator whether the PRODUCTION legacy
// hosting path (LegacyHostingContent.swift) leaves the chat footer's Retry
// area outside hit-testable bounds when the cell estimate is smaller than the
// rendered content. Mirrors BridgedAssistantFooterV3's inlineError layout.
//
// The host view is a subview pinned top/leading/trailing with NO bottom pin
// (production design), so its frame follows the SwiftUI content height while
// the content view's bounds follow the cell estimate. UIKit hitTest only
// descends into subviews after the RECEIVER's pointInside passes, so anything
// sticking below the content view's bounds is both clipped and touch-dead.
//
// NOT an iOS 15 runtime and NOT a full-app XCUITest.

@MainActor
final class ProbeState: ObservableObject {
    var taps: Int = 0
}

private struct RetryFooterContent: View {
    let error: String
    var onRetry: () -> Void

    var body: some View {
        HStack(alignment: .center, spacing: 8) {
            HStack(spacing: 6) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.red)
                Text(error)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
            }
            .contentShape(Rectangle())

            Spacer()

            Button(action: onRetry) {
                HStack(spacing: 4) {
                    Image(systemName: "arrow.clockwise")
                        .font(.caption.weight(.semibold))
                    Text("Retry")
                        .font(.caption.weight(.semibold))
                }
                .foregroundStyle(.black)
                .padding(.horizontal, 12)
                .padding(.vertical, 6)
                .background(Color.gray.opacity(0.25))
                .clipShape(Capsule())
            }
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

@main
@MainActor
final class RetryProbeApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("retry-probe", isDirectory: true)
    let errorText = "Provider error: ⚠️ GPT 5.6 Sol (Careke): Rate limited ⚠️ GPT 6 Astra (Careke): Rate limited..."

    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UIView.setAnimationsEnabled(false)
        let window = UIWindow(frame: UIScreen.main.bounds)
        self.window = window
        window.rootViewController = UIViewController()
        window.makeKeyAndVisible()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        Task { await run() }
        return true
    }

    /// Every view under a root, depth-first.
    func descendants(of root: UIView) -> [UIView] {
        var result: [UIView] = []
        var stack = [root]
        while let view = stack.popLast() {
            result.append(view)
            stack.append(contentsOf: view.subviews)
        }
        return result
    }

    /// Is `view` inside `subtreeRoot`'s hierarchy?
    func isInside(_ view: UIView?, subtreeOf root: UIView?) -> Bool {
        guard let view, let root else { return false }
        var cursor: UIView? = view
        while let c = cursor {
            if c === root { return true }
            cursor = c.superview
        }
        return false
    }

    func run() async {
        func makeProbe(estimateHeight: CGFloat) async -> [String: Any] {
            let state = ProbeState()
            let container = UIView(frame: CGRect(x: 0, y: 150, width: 396, height: estimateHeight))
            container.backgroundColor = .white
            window?.rootViewController?.view.addSubview(container)

            let config = LegacyHostingConfiguration(
                content: AnyView(RetryFooterContent(error: errorText, onRetry: { state.taps += 1 })),
                parent: WeakHostingParent(window?.rootViewController),
                onSizeChange: { _ in }
            )
            let contentView = config.makeContentView()
            contentView.frame = container.bounds
            contentView.autoresizingMask = [.flexibleWidth, .flexibleHeight]
            container.addSubview(contentView)
            container.layoutIfNeeded()
            contentView.layoutIfNeeded()

            // Two layout passes with a delay, mirroring the async geometry
            // report cadence the production path uses.
            try? await Task.sleep(nanoseconds: 200_000_000)
            contentView.setNeedsLayout()
            contentView.layoutIfNeeded()
            container.layoutIfNeeded()
            try? await Task.sleep(nanoseconds: 200_000_000)
            contentView.layoutIfNeeded()

            // The hosting view is the only subview of the content view (the
            // production layout pins it top/leading/trailing).
            let hostView: UIView? = contentView.subviews.first
            let overflow = (hostView?.frame.maxY ?? 0) - contentView.bounds.maxY

            // Retry capsule sits at the trailing edge, vertically centered in
            // the CONTENT (host view), which is pinned to the top of the
            // content view. Its true center is therefore
            //   x = bounds.maxX - 36, y = hostView.frame.midY
            // (hostView.frame is in contentView coordinates).
            let hostCenterY = hostView.map { $0.frame.midY } ?? 25
            let retryPointInContent = CGPoint(x: contentView.bounds.maxX - 36,
                                              y: hostCenterY)
            let iconPointInContent = CGPoint(x: 18, y: 8)
            let retryInWindow = window?.convert(retryPointInContent, from: contentView)
            let iconInWindow = window?.convert(iconPointInContent, from: contentView)
            let retryHit = retryInWindow.flatMap { window?.hitTest($0, with: nil) }
            let iconHit = iconInWindow.flatMap { window?.hitTest($0, with: nil) }

            let result: [String: Any] = [
                "estimate_height": estimateHeight,
                "contentView_bounds_height": Double(contentView.bounds.height),
                "host_view_height": Double(hostView?.bounds.height ?? -1),
                "host_overflow_below_contentView_pt": Double(overflow),
                "host_overflow_expected_when_estimate_short": estimateHeight < 60,
                "retry_point_hit_is_inside_hosting": isInside(retryHit, subtreeOf: hostView),
                "retry_point_hit_chain_tail": String(describing: retryHit.map { String(describing: type(of: $0)) }),
                "icon_point_control_hit_is_inside_hosting": isInside(iconHit, subtreeOf: hostView),
            ]
            container.removeFromSuperview()
            return result
        }

        do {
            // Phase A: cell estimate (20pt) SHORTER than the rendered footer
            // (≈50pt) so the Retry button's true center (y≈25) falls BELOW the
            // content view's bounds — the pre-fix dead zone.
            let short = await makeProbe(estimateHeight: 20)
            // Phase B: estimate (64pt) at/above the content's natural height.
            let natural = await makeProbe(estimateHeight: 64)

            // Green condition (post-fix): the Retry point stays reachable
            // inside the hosting tree at BOTH estimates.
            let shortHit = (short["retry_point_hit_is_inside_hosting"] as? Bool) ?? false
            let naturalHit = (natural["retry_point_hit_is_inside_hosting"] as? Bool) ?? false

            let passed = shortHit && naturalHit
            let shortHitLabel = shortHit ? "hittable" : "dead"
            let report: [String: Any] = [
                "os": UIDevice.current.systemVersion,
                "passed": passed,
                "short_estimate": short,
                "natural_estimate": natural,
                "conclusion": passed
                    ? "PASS: with a 20pt estimate the retry center stays \(shortHitLabel) inside hosting; control estimate also hittable."
                    : "FAIL: short_estimate=\(short) natural_estimate=\(natural)",
                "limits": "Production LegacyHostingConfiguration on pinned iOS26.2; not an iOS15 runtime or full-app XCUITest.",
            ]
            if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) {
                try? data.write(to: directory.appendingPathComponent("report.json"))
            }
            print(String(data: try! JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]), encoding: .utf8)!)
            exit(passed ? 0 : 1)
        }
    }
}