#if MINIS_ROOT_MANAGEMENT && os(iOS)
import Combine
import Foundation
import UIKit

@objc(DeviceMCPNativeBridge)
public final class DeviceMCPNativeBridge: NSObject {
    @objc public static func available() -> Bool {
        DeviceMCPAvailability.shared.read()
    }

    @objc(request:operation:value:epoch:)
    public static func request(_ id: String, operation: String, value: String?, epoch: UInt64) -> NSDictionary {
        guard !Thread.isMainThread, let request = DeviceMCPRequest(id: id, operation: operation, value: value) else {
            return ["ok": false, "error": "invalid"]
        }
        let transaction = DeviceMCPTransaction(request: request)
        Task { @MainActor in
            DeviceMCPController.shared.present(transaction, callerAlive: { OMDeviceMCPCancellationEpoch() == epoch })
        }
        let result = transaction.wait { OMDeviceMCPCancellationEpoch() != epoch }
        Task { @MainActor in DeviceMCPController.shared.removeFinished(transaction) }
        return result as NSDictionary
    }
}

private final class DeviceMCPAvailability: @unchecked Sendable {
    static let shared = DeviceMCPAvailability()
    private let lock = NSLock()
    private var enabled = false

    func read() -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return enabled
    }

    func set(_ enabled: Bool) {
        lock.lock()
        self.enabled = enabled
        lock.unlock()
    }
}

@MainActor
final class DeviceMCPController: NSObject, ObservableObject {
    static let shared = DeviceMCPController()
    static let serverName = "minis-device"
    @Published private(set) var enabled = false
    @Published private(set) var records: [[String: Any]] = []
    private var gate = DeviceMCPGate()
    private var active: DeviceMCPTransaction?
    private weak var approval: UIAlertController?
    private weak var approvalWindow: UIWindow?
    private var callerAlive: (() -> Bool)?
    private let build = RootManagementBuildIdentity(info: Bundle.main.infoDictionary ?? [:])
    private let bundleID = Bundle.main.bundleIdentifier ?? ""

    private override init() {
        super.init()
        for name in [UIApplication.willResignActiveNotification, UIApplication.didEnterBackgroundNotification,
                     UIScene.willDeactivateNotification] {
            NotificationCenter.default.addObserver(self, selector: #selector(deactivate(_:)), name: name, object: nil)
        }
    }

    var supported: Bool { build != nil && bundleID == "com.openminis.app.root" }

    private var window: UIWindow? {
        guard UIApplication.shared.applicationState == .active else { return nil }
        let scenes = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }
            .filter { $0.activationState == .foregroundActive }
        guard scenes.count == 1 else { return nil }
        return scenes[0].windows.first { $0.isKeyWindow && !$0.isHidden }
    }

    func setEnabled(_ value: Bool) {
        if !value { revoke(); return }
        guard supported, window != nil else { return }
        gate.setEnabled(true)
        enabled = true
        DeviceMCPAvailability.shared.set(true)
    }

    @objc private func deactivate(_: Notification) { revoke() }

    private func revoke() {
        DeviceMCPAvailability.shared.set(false)
        gate.setEnabled(false)
        enabled = false
        if let active { complete(active, decision: "revoked") }
    }

    func clearReport() { if active == nil { records.removeAll() } }

    var report: String? {
        guard let build else { return nil }
#if targetEnvironment(simulator)
        let runtime = "simulator"
#elseif targetEnvironment(macCatalyst)
        let runtime = "mac-catalyst"
#else
        let runtime = ProcessInfo.processInfo.isiOSAppOnMac ? "ios-app-on-mac" : "physical-device"
#endif
        let object: [String: Any] = ["schema": 1, "kind": "minis-device-mcp", "bundleIdentifier": bundleID,
                                     "runtime": runtime,
                                     "osVersion": ProcessInfo.processInfo.operatingSystemVersionString,
                                     "build": ["commit": build.commit, "actionsRunID": build.actionsRunID], "records": records]
        return (try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys]))
            .flatMap { String(data: $0, encoding: .utf8) }
    }

    func present(_ transaction: DeviceMCPTransaction, callerAlive: @escaping () -> Bool) {
        let request = transaction.request
        guard transaction.canPresent else { return }
        if let error = gate.begin(request, foreground: supported && window != nil) {
            _ = transaction.finish { transaction.response(error: error) }
            return
        }
        active = transaction
        self.callerAlive = callerAlive
        guard callerAlive(), let window, var presenter = window.rootViewController else {
            complete(transaction, decision: "unavailable"); return
        }
        while let child = presenter.presentedViewController { presenter = child }
        guard !(presenter is UIAlertController), !presenter.isBeingDismissed,
              presenter.viewIfLoaded?.window === window else { complete(transaction, decision: "unavailable"); return }
        let message: String
        switch request.operation {
        case .device: message = AppLocalized("Share this device’s model and iOS version with the requesting tool?")
        case .screen: message = AppLocalized("Share the screen dimensions and scale with the requesting tool?")
        case .brightness: message = AppLocalized("Share the current display brightness with the requesting tool?")
        case .setBrightness: message = AppLocalized("Set display brightness?") + " \(request.percent!)%"
        }
        let alert = UIAlertController(title: AppLocalized("Minis device tools"), message: message, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: AppLocalized("Deny"), style: .cancel) { [weak self] _ in
            self?.complete(transaction, decision: "denied")
        })
        alert.addAction(UIAlertAction(title: AppLocalized("Allow once"), style: .default) { [weak self] _ in
            self?.complete(transaction, decision: "allowed")
        })
        alert.preferredAction = alert.actions.first
        approval = alert
        approvalWindow = window
        presenter.present(alert, animated: true)
    }

    func removeFinished(_ transaction: DeviceMCPTransaction) {
        guard active === transaction else { return }
        complete(transaction, decision: "expired")
    }

    private func complete(_ transaction: DeviceMCPTransaction, decision: String) {
        guard active === transaction else { return }
        let request = transaction.request
        let window = self.window
        let foreground = supported && window != nil && approvalWindow === window
        let allow = gate.end(request, allow: decision == "allowed", foreground: foreground)
        let result = transaction.finish {
            guard decision == "allowed", allow, enabled, let screen = window?.windowScene?.screen, callerAlive?() == true else {
                return transaction.response(error: decision == "allowed" ? "revoked" : decision)
            }
            let data: [String: Any]
            switch request.operation {
            case .device:
                let device = UIDevice.current
                data = ["model": device.model, "systemName": device.systemName, "systemVersion": device.systemVersion]
            case .screen:
                data = ["widthPoints": screen.bounds.width, "heightPoints": screen.bounds.height, "scale": screen.scale]
            case .brightness: data = ["brightness": screen.brightness]
            case .setBrightness:
                screen.brightness = CGFloat(request.percent!) / 100
                data = ["brightness": screen.brightness]
            }
            return ["ok": true, "request_id": request.id.uuidString.lowercased(), "operation": request.operation.rawValue,
                    "data": data]
        }
        let executed = result["ok"] as? Bool == true
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        var record: [String: Any] = ["requestID": request.id.uuidString.lowercased(), "operation": request.operation.rawValue,
                                     "outcome": executed ? "allowed" : (result["error"] as? String ?? "unavailable"),
                                     "executed": executed, "recordedAt": formatter.string(from: Date())]
        if let percent = request.percent { record["requestedPercent"] = percent }
        if let data = result["data"] { record["data"] = data }
        records.append(record)
        if records.count > 64 { records.removeFirst() }
        if let approval, approval.presentingViewController != nil { approval.dismiss(animated: false) }
        approval = nil
        approvalWindow = nil
        callerAlive = nil
        active = nil
    }
}
#endif
