import UIKit
import SwiftUI

// Component-contract probe, NOT screenshot reproduction. The real production
// LegacyHostingContentView is compiled with only contentSizeChanged's access
// widened in a generated copy. Explicit size samples exercise its dispatch /
// coalescing / configuration-ownership code on a real Apple runtime. No cell
// coordinates, layout cache values, or reported screenshot heights are injected.

private struct Delivery: Codable, Equatable {
    let owner: String
    let width: Double
    let height: Double
}

@MainActor
private final class Recorder {
    var deliveries: [Delivery] = []
    func accept(_ size: CGSize, owner: String) {
        deliveries.append(Delivery(owner: owner, width: Double(size.width), height: Double(size.height)))
    }
}

private struct CaseResult: Codable {
    let name: String
    let repetition: Int
    let passed: Bool
    let expectation: String
    let deliveries: [Delivery]
}

@main
@MainActor
final class SizeDeliveryProbeApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = UIViewController()
        window.makeKeyAndVisible()
        self.window = window
        Task { await run() }
        return true
    }

    private func makeConfiguration(_ recorder: Recorder, owner: String) -> LegacyHostingConfiguration {
        LegacyHostingConfiguration(content: AnyView(EmptyView()), parent: WeakHostingParent(nil),
                                   onSizeChange: { size in recorder.accept(size, owner: owner) })
    }

    private func drainScheduledNotifications() async {
        // The production method enqueues one main-queue block. FIFO barriers
        // observe that block deterministically; a second hop catches work it
        // might enqueue. This is not a claim to drain all SwiftUI rendering.
        for _ in 0..<2 {
            await withCheckedContinuation { (continuation: CheckedContinuation<Void, Never>) in
                DispatchQueue.main.async { continuation.resume() }
            }
        }
    }

    private func runCase(_ name: String, repetition: Int) async -> CaseResult {
        let recorder = Recorder()
        // Intentionally unmounted: this is a notification contract, not a
        // rendering oracle. Only the explicit test-seam samples drive it.
        let view = LegacyHostingContentView(configuration: makeConfiguration(recorder, owner: "A"))
        let sample: (CGFloat) -> CGSize = { CGSize(width: 320, height: $0) }
        let expected: String
        switch name {
        case "single-sample-control":
            view.contentSizeChanged(sample(48))
            expected = "one A delivery, height48"
        case "duplicate-sample-control":
            view.contentSizeChanged(sample(48))
            view.contentSizeChanged(sample(48))
            expected = "one A delivery, height48; identical sample is deduplicated"
        case "coalesced-shrink":
            view.contentSizeChanged(sample(72))
            view.contentSizeChanged(sample(36))
            expected = "one A delivery containing the latest height36, not the first height72"
        case "coalesced-growth":
            view.contentSizeChanged(sample(36))
            view.contentSizeChanged(sample(72))
            expected = "one A delivery containing the latest height72, not the first height36"
        case "replacement-without-new-sample":
            view.contentSizeChanged(sample(96))
            view.configuration = makeConfiguration(recorder, owner: "B")
            expected = "no B delivery for an A-root measurement; cancellation or delivery to A is allowed"
        case "replacement-with-new-sample":
            view.contentSizeChanged(sample(96))
            view.configuration = makeConfiguration(recorder, owner: "B")
            view.contentSizeChanged(sample(36))
            expected = "B receives height36 only; no A-root height96 is relabeled as B"
        default:
            fatalError("Unknown test case")
        }
        await drainScheduledNotifications()
        withExtendedLifetime(view) {}
        let values = recorder.deliveries
        let passed: Bool
        switch name {
        case "single-sample-control", "duplicate-sample-control":
            passed = values == [Delivery(owner: "A", width: 320, height: 48)]
        case "coalesced-shrink":
            passed = values == [Delivery(owner: "A", width: 320, height: 36)]
        case "coalesced-growth":
            passed = values == [Delivery(owner: "A", width: 320, height: 72)]
        case "replacement-without-new-sample":
            passed = values.allSatisfy { $0.owner != "B" }
        case "replacement-with-new-sample":
            let current = values.filter { $0.owner == "B" }
            passed = !current.isEmpty && current.allSatisfy { $0.width == 320 && $0.height == 36 }
        default:
            passed = false
        }
        return CaseResult(name: name, repetition: repetition, passed: passed,
                          expectation: expected, deliveries: values)
    }

    private func run() async {
        guard let runID = ProcessInfo.processInfo.environment["PROBE_RUN_ID"],
              UUID(uuidString: runID) != nil else {
            print("INVALID: missing unique PROBE_RUN_ID")
            exit(2)
        }
        var results: [CaseResult] = []
        let names = ["single-sample-control", "duplicate-sample-control", "coalesced-shrink",
                     "coalesced-growth", "replacement-without-new-sample", "replacement-with-new-sample"]
        for repetition in 1...3 {
            for name in names {
                results.append(await runCase(name, repetition: repetition))
            }
        }
        let controlsPassed = results.filter { $0.name.hasSuffix("control") }.allSatisfy(\.passed)
        let passed = controlsPassed && results.allSatisfy(\.passed)
        struct Report: Codable {
            let runID: String
            let os: String
            let controlsPassed: Bool
            let passed: Bool
            let limits: String
            let cases: [CaseResult]
        }
        let report = Report(runID: runID, os: UIDevice.current.systemVersion,
                            controlsPassed: controlsPassed, passed: passed,
                            limits: "Unmodified production delivery implementation with test-only access widening, explicit size samples, real main queue on iOS26.2. Not UICollectionView/full-app/iOS15 rendering or screenshot reproduction.",
                            cases: results)
        do {
            let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("size-delivery-probe", isDirectory: true)
                .appendingPathComponent(runID, isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(report)
            try data.write(to: directory.appendingPathComponent("report.json"), options: .atomic)
            print(String(decoding: data, as: UTF8.self))
            exit(controlsPassed ? (passed ? 0 : 1) : 2)
        } catch {
            print("INVALID: could not persist fresh probe report: \(error)")
            exit(2)
        }
    }
}
