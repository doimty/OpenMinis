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

private struct TallRetryFooter: View {
    let error: String
    var onRetry: () -> Void

    // Mirrors the production footer's load-bearing shape (legacy host, intrinsic
    // height) but pushes the Retry capsule to the BOTTOM of a 96pt ideal height
    // so a 40pt cell frame leaves the capsule below the cell's bounds while it
    // stays inside the hosting view's bounds — the exact overflow tail the
    // cell-level point(inside:) extension must keep reachable.
    var body: some View {
        VStack(spacing: 0) {
            Color.clear.frame(height: 44)
            RetryFooterContent(error: error, onRetry: onRetry)
        }
        .frame(height: 96)
    }
}

/// Plain UICollectionViewCell mirroring SelfSizingCell's load-bearing parts:
/// clip-to-bounds contentView + legacy hosting content view pinned inside it.
/// point(inside:) mirrors the production fix (SelfSizingCell, commit being
/// validated) so this probe exercises the real UIKit hit-test chain end to
/// end: cell gate → LegacyHostingContentView forwarding → host.view.
@MainActor
final class ProbeCell: UICollectionViewCell {
    var hostingContentView: UIView?

    func install(error: String, onRetry: @escaping () -> Void) {
        let config = LegacyHostingConfiguration(
            content: AnyView(TallRetryFooter(error: error, onRetry: onRetry)),
            parent: WeakHostingParent(nil),
            onSizeChange: { _ in }
        )
        let hosted = config.makeContentView()
        hostingContentView = hosted
        hosted.translatesAutoresizingMaskIntoConstraints = false
        contentView.addSubview(hosted)
        // Mirror the production constraints: top/leading/trailing only, so the
        // hosting view grows to its intrinsic height instead of the cell's
        // frame — the overflow window the cell hit-region extension covers.
        NSLayoutConstraint.activate([
            hosted.leadingAnchor.constraint(equalTo: contentView.leadingAnchor),
            hosted.trailingAnchor.constraint(equalTo: contentView.trailingAnchor),
            hosted.topAnchor.constraint(equalTo: contentView.topAnchor),
        ])
        contentView.layoutIfNeeded()
        hosted.setNeedsLayout()
        hosted.layoutIfNeeded()
    }

    // Mirror of SelfSizingCell.point(inside:with:) — extends the hit region to
    // the hosted content's real bounds on the legacy path so a short estimate
    // does not make the overflow tail touch-dead.
    override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {
        if super.point(inside: point, with: event) { return true }
        guard let legacy = hostingContentView as? LegacyHostingContentView else {
            return false
        }
        return legacy.hitRegionContains(convert(point, to: legacy))
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

        // Phase D (cell-level dead zone — the gap 762a178's content-view-only
        // forwarding could not close): SHORT cell frame (40pt) while the legacy
        // hosting view overflows to its intrinsic 96pt height. The Retry
        // capsule center sits ~70pt into the cell's coordinate space — BELOW
        // the cell's bounds (40pt) but INSIDE the hosting view's bounds (96pt).
        // Without the cell-level point(inside:) extension UIKit rejects the
        // tap at the cell edge (visible but touch-dead, the user's
        // "Retry/Resume untappable" symptom); with it the tap reaches the
        // hosting tree.
        let (cellD, _) = makeScene(cellY: 220, overlayVisible: false)
        cellD.frame = CGRect(x: 0, y: 220, width: width, height: 40)
        try? await Task.sleep(nanoseconds: 300_000_000)
        let dPoint = cellD.convert(CGPoint(x: cellD.bounds.maxX - 36, y: 70), to: window)
        let dHit = window?.hitTest(dPoint, with: nil)
        let dInsideHost = isInside(dHit, subtreeOf: cellD.hostingContentView)
        let dDeadZone = dHit == nil
        cellD.removeFromSuperview()

        let report: [String: Any] = [
            "os": UIDevice.current.systemVersion,
            "phase_A_control_retry_inside_cell": aInsideCell,
            "phase_B_short_inset_retry_hits_overlay": bHitsOverlay,
            "phase_B_short_inset_retry_inside_cell": bInsideCell,
            "phase_C_fixed_inset_retry_inside_cell": cInsideCell,
            "phase_C_fixed_inset_retry_hits_overlay": cHitsOverlay,
            "phase_D_short_cell_retry_inside_host": dInsideHost,
            "phase_D_short_cell_retry_dead_zone": dDeadZone,
            "overlay_height_pt": overlayHeight,
            "limits": "Full hierarchy (footer cell + composer overlay) on pinned iOS26.2; not an iOS15 runtime or full-app XCUITest.",
        ]
        // Green: control reachable, short-inset tap lands on the overlay (bug
        // reproduced), fixed-inset tap reaches the cell again, and the
        // short-cell overflow tail stays reachable through the cell hit-region
        // extension (no dead zone).
        let passed = aInsideCell && bHitsOverlay && !bInsideCell && cInsideCell
            && !cHitsOverlay && dInsideHost && !dDeadZone
        let full: [String: Any] = ["os": UIDevice.current.systemVersion, "passed": passed, "details": report]
        if let data = try? JSONSerialization.data(withJSONObject: full, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: directory.appendingPathComponent("report.json"))
        }
        print(String(data: try! JSONSerialization.data(withJSONObject: full, options: [.prettyPrinted, .sortedKeys]), encoding: .utf8)!)
        exit(passed ? 0 : 1)
    }
}