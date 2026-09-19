import SwiftUI
import UIKit

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

private struct LegacyHostedSizeKey: PreferenceKey {
    static var defaultValue: CGSize = .zero
    static func reduce(value: inout CGSize, nextValue: () -> CGSize) { value = nextValue() }
}

private struct LegacyHostedRoot: View {
    let content: AnyView
    let onSizeChange: (CGSize) -> Void

    var body: some View {
        content
            .fixedSize(horizontal: false, vertical: true)
            .background(GeometryReader { proxy in
                Color.clear.preference(key: LegacyHostedSizeKey.self, value: proxy.size)
            })
            .onPreferenceChange(LegacyHostedSizeKey.self, perform: onSizeChange)
    }
}

@MainActor
private final class LegacyHostingContentView: UIView, UIContentView {
    private var current: LegacyHostingConfiguration
    private let host = UIHostingController(rootView: AnyView(EmptyView()))
    private var lastSize: CGSize = .zero
    private var lastWidth: CGFloat = 0
    private var sizeNotificationPending = false

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
        lastSize = .zero
        host.rootView = AnyView(LegacyHostedRoot(content: current.content) { [weak self] size in
            self?.contentSizeChanged(size)
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

    private func contentSizeChanged(_ size: CGSize) {
        guard size.width > 1, size.height.isFinite,
              abs(size.width - lastSize.width) > 0.5 || abs(size.height - lastSize.height) > 0.5 else { return }
        lastSize = size
        guard !sizeNotificationPending else { return }
        sizeNotificationPending = true
        // Never invalidate a collection layout from inside SwiftUI's measure.
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.sizeNotificationPending = false
            self.invalidateIntrinsicContentSize()
            self.current.onSizeChange(size)
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
