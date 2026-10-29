import UIKit
import Foundation
import ObjectiveC

@main
@MainActor
final class DebugBridgeProbeApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?

    func application(_ application: UIApplication,
                     didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let window = UIWindow(frame: UIScreen.main.bounds)
        window.rootViewController = UIViewController()
        window.makeKeyAndVisible()
        self.window = window
        DispatchQueue.global().asyncAfter(deadline: .now() + 30) {
            // An independent watchdog. Missing report is INVALID, never a
            // successful reproduction or a green result.
            FileHandle.standardError.write(Data("INVALID: native bridge probe watchdog\n".utf8))
            exit(2)
        }
        Task { await run() }
        return true
    }

    private func parse(_ text: String) -> [String: Any] {
        (try? JSONSerialization.jsonObject(with: Data(text.utf8))) as? [String: Any] ?? [:]
    }

    private func run() async {
        guard let runID = ProcessInfo.processInfo.environment["PROBE_RUN_ID"], UUID(uuidString: runID) != nil,
              let variant = ProcessInfo.processInfo.environment["PROBE_VARIANT"],
              ["baseline", "candidate"].contains(variant) else {
            print("INVALID: missing run identity"); exit(2)
        }
        // Cold lookup happens before any typed reference/singleton call in
        // this method. Do not let forced Swift realization hide the failure.
        let coldDispatcher = NSClassFromString("DebugLocalDispatch") != nil
        let coldReader = NSClassFromString("MinisDebugLogReader") != nil
        let requestObject: [String: Any] = ["jsonrpc": "2.0", "id": 1,
            "method": "fixture.echo", "params": ["nonce": runID]]
        let requestData = try! JSONSerialization.data(withJSONObject: requestObject, options: [.sortedKeys])
        let request = String(decoding: requestData, as: UTF8.self)
        let background = await Task.detached { () -> (String, Bool) in
            let onMain = Thread.isMainThread
            return (ProbeCallLocalDispatcher(request), onMain)
        }.value
        let backgroundJSON = parse(background.0)
        let fixture = backgroundJSON["result"] as? [String: Any]
        let backgroundOK = fixture?["fixtureRPC"] as? Bool == true
            && fixture?["unchangedEnvelope"] as? String == request
        let backgroundError = (backgroundJSON["error"] as? [String: Any])?["message"] as? String

        let dispatcherType: AnyClass = DebugLocalDispatch.self
        let readerType: AnyClass = MinisDebugLogReader.self
        let dispatcherName = NSStringFromClass(dispatcherType)
        let readerName = NSStringFromClass(readerType)
        func sameClass(_ value: AnyClass?, _ expected: AnyClass) -> Bool {
            guard let value else { return false }
            return ObjectIdentifier(value) == ObjectIdentifier(expected)
        }
        let typedControls = sameClass(NSClassFromString(dispatcherName), dispatcherType)
            && sameClass(NSClassFromString(readerName), readerType)
        let warmDispatcher = sameClass(NSClassFromString("DebugLocalDispatch"), dispatcherType)
        let warmReader = sameClass(NSClassFromString("MinisDebugLogReader"), readerType)
        let selectorControls = class_getClassMethod(dispatcherType, NSSelectorFromString("sharedInstance")) != nil
            && class_getInstanceMethod(dispatcherType, NSSelectorFromString("dispatchWithEnvelopeJSON:")) != nil
            && class_getClassMethod(readerType, NSSelectorFromString("sharedInstance")) != nil
            && class_getInstanceMethod(readerType, NSSelectorFromString("readLogsJSONWithLastN:minutes:grep:")) != nil
        let mainReply = parse(DebugLocalDispatch.shared.dispatch(envelopeJSON: request))
        let mainError = mainReply["error"] as? [String: Any]
        let mainGuard = mainError?["code"] as? Int == -32000
            && (mainError?["message"] as? String)?.contains("invoked on main thread") == true
        let controlsOK = typedControls && selectorControls && mainGuard && !background.1
        let passed = controlsOK && coldDispatcher && coldReader && warmDispatcher && warmReader && backgroundOK
        let report: [String: Any] = [
            "runID": runID, "variant": variant, "os": UIDevice.current.systemVersion,
            "controlsPassed": controlsOK, "passed": passed,
            "coldDispatcherLookup": coldDispatcher, "coldLogReaderLookup": coldReader,
            "warmDispatcherIdentity": warmDispatcher, "warmLogReaderIdentity": warmReader,
            "runtimeDispatcherName": dispatcherName, "runtimeLogReaderName": readerName,
            "typedQualifiedControls": typedControls, "selectorControls": selectorControls,
            "mainThreadGuard": mainGuard, "backgroundWasMain": background.1,
            "backgroundDispatcherReachedRPC": backgroundOK,
            "backgroundError": (backgroundError as Any?) ?? NSNull(),
            "limits": "Real production Swift bridges and C dispatch helper; only downstream RPC/inspector/log-manager services are fixtures. No UI layout, log retrieval, network auth or device iOS15 acceptance is asserted."
        ]
        do {
            let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("debug-bridge-probe", isDirectory: true)
                .appendingPathComponent(runID, isDirectory: true)
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: directory.appendingPathComponent("report.json"), options: .atomic)
            print(String(decoding: data, as: UTF8.self))
            exit(controlsOK ? (passed ? 0 : 1) : 2)
        } catch {
            print("INVALID: report write failed: \(error)"); exit(2)
        }
    }
}
