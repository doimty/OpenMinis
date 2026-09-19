import UIKit
import SwiftUI

// Full-hierarchy retry hit-test probe.
//
// Reproduces the PRODUCTION chat stack shape on the pinned simulator:
//   window
//   ├─ footer cell: ProbeCell → LegacyHostingContentView → host.view with
//   │   the BridgedAssistantFooterV3 inlineError layout (red box + Retry
//   │   capsule at the trailing edge)
//   └─ composer overlay (transparent container, bottom-anchored, 180pt tall)
//       placed ABOVE the cell in z-order — mirrors AIChatView's
//       `.overlay(alignment: .bottom) { VStack { floatingToolPreview;
//       inputBar } }`.
//
// When the bottom contentInset is correct, the last footer cell ends above the
// composer, so the Retry capsule is reachable. When the inset is short (the
// floating preview's geometry never reported → floatingBarHeight stays 0 and
// the old code inset by only inputBar+8), the footer sits UNDER the overlay
// and the overlay intercepts the tap — the user's exact symptom.
//
// Phases:
//   A control: no overlay → Retry point hits inside the cell hosting tree.
//   B bug    : short inset → cell placed under a 180pt overlay → Retry point
//              hits the OVERLAY (red).
//   C fixed  : correct inset → cell above the same overlay → Retry point hits
//              inside the cell again.
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
            .accessibilityIdentifier("retryButton")
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color.red.opacity(0.12))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

/// Plain UICollectionViewCell mirroring SelfSizingCell's load-bearing parts:
/// clip-to-bounds contentView + legacy hosting content view pinned inside it.
@MainActor
final class ProbeCell: UICollectionViewCell {
    var hostingContentView: UIView?

    func install(error: String, onRetry: @escaping () -> Void) {
        let config = LegacyHostingConfiguration(
            content: AnyView(RetryFooterContent(error: error, onRetry: onRetry)),
            parent: WeakHostingParent(nil),
            onSizeChange: { _ in }
        )
        let hosted = config.makeContentView()
        hostingContentView = hosted
        hosted.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(hosted)
        NSLayoutConstraint.activate([
            hosted.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            hosted.trailingAnchor.constraint(equalTo: contentView.trailingAnchor),
            hosted.topAnchor.constraint(equalTo: contentView.topAnchor),
            hosted.bottomAnchor.constraint(equalTo: contentView.bottomAnchor),
        ])
        contentView.layoutIfNeeded()
        hosted.setNeedsLayout()
        hosted.layoutIfNeeded()
    }
}

@main
@MainActor
final class HierarchyRetryProbeApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("retry-hierarchy-probe", isDirectory: true)
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
        let width: CGFloat = 396
        let cellHeight: CGFloat = 64 // generous cell frame; content ~50pt
        let screen = window?.bounds ?? CGRect(x: 0, y: 0, width: 428, height: 926)
        let overlayHeight: CGFloat = 180 // inputBar 115.67 + floating preview ~64

        // Build one cell + overlay pair per phase.
        func makeScene(cellY: CGFloat, overlayVisible: Bool) -> (ProbeCell, UIView) {
            let cell = ProbeCell(frame: CGRect(x: 0, y: cellY, width: width, height: cellHeight))
            window?.rootViewController?.view.addSubview(cell)
            cell.install(error: errorText, onRetry: {})

            let overlay = UIView(frame: CGRect(x: 0, y: screen.height - overlayHeight,
                                               width: screen.width, height: overlayHeight))
            overlay.backgroundColor = UIColor(white: 0.92, alpha: 1)
            overlay.accessibilityIdentifier = "composerOverlay"
            if overlayVisible {
                window?.rootViewController?.view.addSubview(overlay)
            }
            return (cell, overlay)
        }

        func retryPointInWindow(of cell: ProbeCell) -> CGPoint {
            // Retry capsule: trailing edge, vertically centered in the 50pt
            // content (host pinned top). Center ≈ (maxX - 36, 25) in the
            // cell's coordinate space.
            cell.convert(CGPoint(x: cell.bounds.maxX - 36, y: 25), to: window)
        }

        // Phase A (control): cell mid-screen, NO overlay.
        let (cellA, _) = makeScene(cellY: 220, overlayVisible: false)
        try? await Task.sleep(nanoseconds: 300_000_000)
        let aPoint = retryPointInWindow(of: cellA)
        let aHit = window?.hitTest(aPoint, with: nil)
        let aInsideCell = isInside(aHit, subtreeOf: cellA.hostingContentView)
        cellA.removeFromSuperview()

        // Phase B (bug): cell placed UNDER the overlay (short inset).
        let (cellB, overlayB) = makeScene(cellY: screen.height - overlayHeight + 64, overlayVisible: true)
        try? await Task.sleep(nanoseconds: 300_000_000)
        let bPoint = retryPointInWindow(of: cellB)
        let bHit = window?.hitTest(bPoint, with: nil)
        let bHitsOverlay = isInside(bHit, subtreeOf: overlayB)
        let bInsideCell = isInside(bHit, subtreeOf: cellB.hostingContentView)
        cellB.removeFromSuperview(); overlayB.removeFromSuperview()

        // Phase C (fixed): cell ABOVE the same overlay (correct inset).
        let (cellC, overlayC) = makeScene(cellY: screen.height - overlayHeight - cellHeight - 12, overlayVisible: true)
        try? await Task.sleep(nanoseconds: 300_000_000)
        let cPoint = retryPointInWindow(of: cellC)
        let cHit = window?.hitTest(cPoint, with: nil)
        let cInsideCell = isInside(cHit, subtreeOf: cellC.hostingContentView)
        let cHitsOverlay = isInside(cHit, subtreeOf: overlayC)
        cellC.removeFromSuperview(); overlayC.removeFromSuperview()

        let report: [String: Any] = [
            "os": UIDevice.current.systemVersion,
            "phase_A_control_retry_inside_cell": aInsideCell,
            "phase_B_short_inset_retry_hits_overlay": bHitsOverlay,
            "phase_B_short_inset_retry_inside_cell": bInsideCell,
            "phase_C_fixed_inset_retry_inside_cell": cInsideCell,
            "phase_C_fixed_inset_retry_hits_overlay": cHitsOverlay,
            "overlay_height_pt": overlayHeight,
            "limits": "Full hierarchy (footer cell + composer overlay) on pinned iOS26.2; not an iOS15 runtime or full-app XCUITest.",
        ]
        // Green: control reachable, short-inset tap lands on the overlay (bug
        // reproduced), fixed-inset tap reaches the cell again.
        let passed = aInsideCell && bHitsOverlay && !bInsideCell && cInsideCell && !cHitsOverlay
        let full: [String: Any] = ["os": UIDevice.current.systemVersion, "passed": passed, "details": report]
        if let data = try? JSONSerialization.data(withJSONObject: full, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: directory.appendingPathComponent("report.json"))
        }
        print(String(data: try! JSONSerialization.data(withJSONObject: full, options: [.prettyPrinted, .sortedKeys]), encoding: .utf8)!)
        exit(passed ? 0 : 1)
    }
}