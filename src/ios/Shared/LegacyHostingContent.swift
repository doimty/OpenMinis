import Combine
import SwiftUI
import UIKit

/// Hosted UIKit renderers may need one layout pass before a GeometryReader
/// height is safe to promote into UICollectionView's row cache. Plain SwiftUI
/// content does not conform, so it keeps the existing first-report behavior.
protocol LegacyHostedMeasurementReadiness: AnyObject {
    var legacyHostedMeasurementReady: Bool { get }
    var legacyHostedMeasurementDidBecomeReady: (() -> Void)? { get set }
}

/// UIContentConfiguration is available on iOS 14. Keep the cell's existing
/// configuration/generation ownership instead of managing a second cell tree.
@MainActor
struct LegacyHostingConfiguration: UIContentConfiguration {
    let content: AnyView
    let parent: WeakHostingParent
    let onSizeChange: (CGSize) -> Void

    func makeContentView() -> UIView & UIContentView {
        LegacyHostingContentView(configuration: self)
    }

    func updated(for state: UIConfigurationState) -> LegacyHostingConfiguration { self }
}

@MainActor
final class WeakHostingParent {
    weak var value: UIViewController?
    init(_ value: UIViewController?) { self.value = value }
}

private final class LegacyHostedMeasurementRevision: ObservableObject {
    @Published var value: UInt = 0
}

private struct LegacyHostedSize: Equatable {
    let generation: UInt
    let revision: UInt
    let size: CGSize
}

private struct LegacyHostedSizeKey: PreferenceKey {
    static var defaultValue = LegacyHostedSize(generation: 0, revision: 0, size: .zero)
    static func reduce(value: inout LegacyHostedSize, nextValue: () -> LegacyHostedSize) {
        value = nextValue()
    }
}

private struct LegacyHostedRoot: View {
    let content: AnyView
    let generation: UInt
    @ObservedObject var revision: LegacyHostedMeasurementRevision
    let onSizeChange: (LegacyHostedSize) -> Void

    var body: some View {
        content
            .fixedSize(horizontal: false, vertical: true)
            // [T-ios15-legacyhost-measure] overlay, not background: a background
            // GeometryReader sits BEHIND the hosted content and on some legacy
            // layout passes reports the parent's proposal (the cell's current
            // estimate, e.g. 40pt) instead of the content's fixedSize ideal
            // height (e.g. 96pt). The callback then writes the estimate back
            // into the cell and the collection view never converges — the
            // composer probe's collection-initial/grow/shrink all stayed red
            // with the cell pinned at the 40pt estimate while the host view
            // rendered at 96pt. overlay is stacked ABOVE the already-laid-out
            // content and reports the real rendered size.
            .overlay(
                GeometryReader { proxy in
                    Color.clear.preference(
                        key: LegacyHostedSizeKey.self,
                        value: LegacyHostedSize(generation: generation, revision: revision.value, size: proxy.size))
                }
            )
            .onPreferenceChange(LegacyHostedSizeKey.self, perform: onSizeChange)
    }
}

@MainActor
final class LegacyHostingContentView: UIView, UIContentView {
    private var current: LegacyHostingConfiguration
    private let host = UIHostingController(rootView: AnyView(EmptyView()))
    private var lastSize: CGSize = .zero
    private var lastWidth: CGFloat = 0
    /// Latest measured size waiting for the main-queue delivery hop. SwiftUI
    /// can publish a shrink/grow again before that hop runs; delivering the
    /// first sample leaves the collection layout holding a stale row height.
    private var pendingSize: CGSize?
    private var sizeNotificationPending = false
    private var configurationGeneration: UInt = 0
    private var pendingNotificationGeneration: UInt = 0
    private var measurementPreferenceRevision: UInt = 0
    private let measurementRevision = LegacyHostedMeasurementRevision()
    private var measurementReadinessRefreshPending = false

    var configuration: any UIContentConfiguration {
        get { current }
        set {
            guard let next = newValue as? LegacyHostingConfiguration else { return }
            current = next
            updateRoot()
        }
    }

    init(configuration: LegacyHostingConfiguration) {
        current = configuration
        super.init(frame: .zero)
        backgroundColor = .clear
        directionalLayoutMargins = .zero
        host.view.backgroundColor = .clear
        host.view.translatesAutoresizingMaskIntoConstraints = false
        addSubview(host.view)
        // Pin only top/leading/trailing. With the bottom edge free, Auto
        // Layout sizes the hosting view to its intrinsic (SwiftUI ideal)
        // height instead of the cell's current frame, so the GeometryReader
        // report is the true content height even while the cell still holds
        // an estimate. Pinning the bottom made the report track the estimate
        // and the iOS 15 chat layout stayed at coarse heights.
        NSLayoutConstraint.activate([
            host.view.leadingAnchor.constraint(equalTo: leadingAnchor),
            host.view.trailingAnchor.constraint(equalTo: trailingAnchor),
            host.view.topAnchor.constraint(equalTo: topAnchor),
        ])
        updateRoot()
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    private func updateRoot() {
        configurationGeneration &+= 1
        pendingSize = nil
        lastSize = .zero
        measurementPreferenceRevision = 0
        measurementRevision.value = 0
        let generation = configurationGeneration
        host.rootView = AnyView(LegacyHostedRoot(
            content: current.content, generation: generation, revision: measurementRevision) { [weak self] payload in
            self?.contentSizeChanged(payload)
        })
        attachIfNeeded()
        host.view.invalidateIntrinsicContentSize()
        invalidateIntrinsicContentSize()
        setNeedsLayout()
    }

    private func attachIfNeeded() {
        guard superview != nil, let parent = current.parent.value,
              host.parent !== parent else { return }
        detach()
        parent.addChild(host)
        host.didMove(toParent: parent)
    }

    private func detach() {
        guard host.parent != nil else { return }
        host.willMove(toParent: nil)
        host.removeFromParent()
    }

    override func didMoveToSuperview() {
        super.didMoveToSuperview()
        if superview == nil { detach() } else { attachIfNeeded() }
    }

    override func didMoveToWindow() {
        super.didMoveToWindow()
        if window == nil { detach() } else { attachIfNeeded() }
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        if abs(bounds.width - lastWidth) > 0.5 {
            lastWidth = bounds.width
            host.view.invalidateIntrinsicContentSize()
            invalidateIntrinsicContentSize()
        }
    }

    /// A short cell estimate leaves the hosting view taller than this content
    /// view's bounds (the bottom edge is intentionally unpinned so intrinsic
    /// height drives the async size report). UIKit hit-testing stops at the
    /// RECEIVER's edge before descending into subviews, so everything in the
    /// overflow tail becomes touch-dead until the layout settles — the chat
    /// footer's Retry capsule is exactly that tail. Keep the whole hosting
    /// view reachable: use the default path first, then forward touches that
    /// land inside host.view's frame even though they fall below our bounds.
    override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? {
        let hit = super.hitTest(point, with: event)
        if hit != nil { return hit }
        let hostPoint = convert(point, to: host.view)
        if host.view.bounds.contains(hostPoint) {
            return host.view.hitTest(hostPoint, with: event)
        }
        return nil
    }

    /// True when `point` (in this view's coordinate space) lies inside the
    /// hosting view's real bounds — i.e. inside the SwiftUI content even when
    /// it falls below this view's own bounds during the pre-measure estimate
    /// window. UIKit rejects touches at the RECEIVER's edge before consulting
    /// subviews, and that receiver is the CELL, not this content view: the
    /// content view's own hitTest override is never consulted for a point
    /// below the cell's bounds. SelfSizingCell therefore extends its hit
    /// region to this region on the iOS 15 legacy path, so the overflow tail
    /// (footer Retry/Resume capsules, the tail of a thinking block) stays
    /// tappable while the async GeometryReader report is in flight.
    func hitRegionContains(_ point: CGPoint) -> Bool {
        let hostPoint = convert(point, to: host.view)
        return host.view.bounds.contains(hostPoint)
    }

    override var intrinsicContentSize: CGSize {
        guard bounds.width > 1 else {
            return CGSize(width: UIView.noIntrinsicMetric, height: UIView.noIntrinsicMetric)
        }
        return measuredSize(width: bounds.width)
    }

    override func systemLayoutSizeFitting(_ targetSize: CGSize,
        withHorizontalFittingPriority horizontalFittingPriority: UILayoutPriority,
        verticalFittingPriority: UILayoutPriority) -> CGSize {
        let width = targetSize.width > 1 ? targetSize.width : bounds.width
        guard width > 1 else { return .zero }
        return measuredSize(width: width)
    }

    private func measuredSize(width: CGFloat) -> CGSize {
        host.view.bounds.size.width = width
        host.view.setNeedsLayout()
        host.view.layoutIfNeeded()
        let fitting = CGSize(width: width, height: UIView.layoutFittingCompressedSize.height)
        let size = host.view.systemLayoutSizeFitting(
            fitting,
            withHorizontalFittingPriority: .required,
            verticalFittingPriority: .fittingSizeLevel)
        return CGSize(width: width, height: max(0, ceil(size.height)))
    }

    private func installMeasurementReadinessObservers() -> [LegacyHostedMeasurementReadiness] {
        var readinessViews: [LegacyHostedMeasurementReadiness] = []
        var pending: [UIView] = [host.view]
        while let view = pending.popLast() {
            if let readiness = view as? LegacyHostedMeasurementReadiness {
                readinessViews.append(readiness)
                readiness.legacyHostedMeasurementDidBecomeReady = { [weak self] in
                    self?.refreshMeasurementPreference()
                }
            }
            pending.append(contentsOf: view.subviews)
        }
        return readinessViews
    }

    private func hostedMeasurementIsReady() -> Bool {
        installMeasurementReadinessObservers().allSatisfy { $0.legacyHostedMeasurementReady }
    }

    private func refreshMeasurementPreference() {
        guard !measurementReadinessRefreshPending else { return }
        // Invalidate the old revision synchronously. A delivery already queued
        // by the provisional preference must see the new revision and bail out
        // before it can reach the cell, even though the root refresh itself is
        // deferred to the next main-queue turn.
        measurementPreferenceRevision &+= 1
        let revision = measurementPreferenceRevision
        measurementReadinessRefreshPending = true
        lastSize = .zero
        pendingSize = nil
        sizeNotificationPending = false
        let generation = configurationGeneration
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.measurementReadinessRefreshPending = false
            guard self.configurationGeneration == generation,
                  self.measurementPreferenceRevision == revision else { return }
            // Publish only the preference revision. The hosted content remains
            // the same SwiftUI tree, so becoming ready cannot recursively
            // recreate a fresh text view and lose the readiness token.
            self.measurementRevision.value = revision
        }
    }

    private func contentSizeChanged(_ payload: LegacyHostedSize) {
        // REENTRY-DIAG-BEGIN
        #if DEBUG
        if ReentryDiagnostics.active {
            let context = reentryDiagnosticContext(self)
            ReentryDiagnostics.shared.record(
                kind: "host-size", owner: context.collection, subject: self, parent: context.cell,
                generation: payload.generation, phase: "preference",
                values: ["width": Double(payload.size.width), "height": Double(payload.size.height),
                         "currentGen": Double(configurationGeneration),
                         "revision": Double(payload.revision),
                         "currentRevision": Double(measurementPreferenceRevision),
                         "previousH": Double(lastSize.height)])
        }
        #endif
        // REENTRY-DIAG-END
        // Reject a delayed preference produced by a superseded root before it
        // can update lastSize or enter the current configuration's callback.
        guard payload.generation == configurationGeneration,
              payload.revision == measurementPreferenceRevision else { return }
        // A GeometryReader can publish the host's finite outer width while a
        // nested UIKit renderer is still answering its unbounded intrinsic
        // query. Do not promote that provisional height into the cell until
        // every renderer that opts into this contract has a finite width.
        // The next real child layout changes the preference and retries here.
        guard hostedMeasurementIsReady() else { return }
        let size = payload.size
        guard size.width > 1, size.height.isFinite,
              abs(size.width - lastSize.width) > 0.5 || abs(size.height - lastSize.height) > 0.5 else { return }
        lastSize = size
        // Keep the newest sample while the layout invalidation is deferred.
        // The previous implementation captured the first sample and dropped
        // later ones, so a tall pre-collapse measurement could remain reserved
        // after the hosted content had already shrunk.
        pendingSize = size
        let generation = configurationGeneration
        guard !sizeNotificationPending || pendingNotificationGeneration != generation else { return }
        sizeNotificationPending = true
        pendingNotificationGeneration = generation
        let revision = payload.revision
        // Never invalidate a collection layout from inside SwiftUI's measure.
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            // A queued callback from the previous content configuration or
            // preference revision must not deliver a stale measurement.
            guard self.configurationGeneration == generation,
                  self.measurementPreferenceRevision == revision else { return }
            self.sizeNotificationPending = false
            let latest = self.pendingSize ?? size
            self.pendingSize = nil
            // REENTRY-DIAG-BEGIN
            #if DEBUG
            if ReentryDiagnostics.active {
                let context = reentryDiagnosticContext(self)
                ReentryDiagnostics.shared.record(
                    kind: "host-size", owner: context.collection, subject: self, parent: context.cell,
                    generation: generation, phase: "delivered",
                    values: ["width": Double(latest.width), "height": Double(latest.height)])
            }
            #endif
            // REENTRY-DIAG-END
            self.invalidateIntrinsicContentSize()
            self.current.onSizeChange(latest)
        }
    }

    deinit {
        // The parent retains child controllers, so balancing containment here
        // is essential when UIKit discards a cell rather than reusing it.
        let controller = host
        DispatchQueue.main.async {
            controller.willMove(toParent: nil)
            controller.removeFromParent()
        }
    }
}
