import Darwin
import Foundation

@main
enum RootManagementAuthorizationTests {
    @MainActor static func main() async {
        var gate = RootManagementAuthorization()
        precondition(!gate.sessionEnabled && gate.pending == nil && gate.running == nil)
        for operation in RootManagementOperation.allCases {
            precondition(!gate.request(operation))
            precondition(!gate.consume(operation))
        }
        gate.setEnabled(true)
        precondition(gate.request(.prepare))
        precondition(!gate.request(.cleanup), "pending confirmation must not be replaced")
        precondition(!gate.consume(.cleanup), "confirmation must match one requested operation")
        gate.cancel()
        precondition(!gate.consume(.prepare), "cancelled operation must never execute")
        precondition(gate.running == nil)
        precondition(gate.request(.check))
        precondition(gate.consume(.check))
        precondition(!gate.consume(.check), "one confirmation cannot execute twice")
        precondition(!gate.request(.cleanup), "no parallel operation")
        gate.revoke()
        precondition(gate.running == .check, "revocation must not pretend to roll back in-flight I/O")
        precondition(!gate.sessionEnabled && gate.pending == nil)
        gate.finish()
        precondition(!gate.request(.identity), "background revocation persists after completion")
        gate.setEnabled(true)
        precondition(gate.request(.cleanup))
        gate.revoke()
        precondition(!gate.consume(.cleanup), "backgrounding revokes an unused confirmation")
        gate.setEnabled(true)
        precondition(gate.request(.identity))
        gate.setEnabled(false)
        precondition(!gate.consume(.identity), "disabling revokes an unused confirmation")
        print("PASS root authorization: disabled default, one exact confirmation, cancel, no replay or parallel work, background/dismissal revocation")
        protocolChecks()
        await workerCheck()
    }

    @MainActor private static func workerCheck() async {
        let build = RootManagementBuildIdentity(info: [
            "MinisRootManagementEnabled": true,
            "MinisRootManagementSchema": 1,
            "MinisRootBuildCommit": String(repeating: "a", count: 40),
            "MinisRootActionsRunID": "1",
        ])!
        let report = await RootManagementRunner.run(
            .identity, build: build, os: "macOS test harness", bundle: "com.openminis.test"
        )
        let transport = report.transport
        precondition(transport.stage == "client.helper-path" && transport.errorNumber == Int(EINVAL),
                     "The worker must pass the native thread check and stop at the non-app test executable: \(transport)")
        precondition(transport.exitCode == -1 && transport.outputBytes == 0 && report.helper == nil && !report.rootObserved)
        print("PASS production Root runner: MainActor caller reaches native helper-path check off the main thread without spawning")
    }

    private static func protocolChecks() {
        let commit = String(repeating: "a", count: 40)
        func success(_ operation: RootManagementOperation) -> [String: Any] {
            var object: [String: Any] = ["schema": 1, "helper_commit": commit, "ok": true,
                "operation": operation.rawValue, "uid": 0, "euid": 0]
            switch operation {
            case .identity:
                object["errno"] = 0
            case .prepare:
                object["marker_id"] = RootHelperProtocol.markerID
            case .check:
                object["marker_id"] = RootHelperProtocol.markerID
                object["device"] = "16777218"
                object["inode"] = "12345"
                object["bytes"] = RootHelperProtocol.sampleBytes
            case .cleanup:
                object["removed"] = true
            }
            return object
        }
        func validate(_ object: [String: Any], _ operation: RootManagementOperation) -> (RootHelperReport?, String?) {
            let data = try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
            return RootHelperProtocol.validate(data, operation: operation, commit: commit)
        }
        func reject(_ operation: RootManagementOperation = .identity,
                    edit: (inout [String: Any]) -> Void) {
            var object = success(operation)
            edit(&object)
            let result = validate(object, operation)
            precondition(result.0 == nil && result.1 != nil, "malformed helper report was accepted: \(object)")
        }
        for operation in RootManagementOperation.allCases {
            let result = validate(success(operation), operation)
            precondition(result.0?.ok == true && result.1 == nil, "valid \(operation) report refused")
        }
        reject { $0["schema"] = 2 }
        reject { $0["helper_commit"] = String(repeating: "b", count: 40) }
        reject { $0["operation"] = "prepare" }
        reject { $0["euid"] = 501 }
        reject { $0["uid"] = -1 }
        reject { $0["uid"] = true }
        reject { $0["ok"] = 1 }
        reject { $0["errno"] = 1 }
        reject { $0["unknown"] = "unexpected" }
        reject { $0["device"] = ["nested": "object"] }
        reject { $0["marker_id"] = NSNull() }
        reject(.prepare) { $0.removeValue(forKey: "marker_id") }
        reject(.prepare) { $0["marker_id"] = "wrong-marker" }
        reject(.check) { $0.removeValue(forKey: "inode") }
        reject(.check) { $0["device"] = "-1" }
        reject(.check) { $0["inode"] = String(repeating: "1", count: 21) }
        reject(.check) { $0["bytes"] = RootHelperProtocol.sampleBytes + 1 }
        reject(.cleanup) { $0["removed"] = false }

        var failure: [String: Any] = ["schema": 1, "helper_commit": commit, "ok": false,
            "operation": "identity", "uid": 501, "euid": 501, "errno": 1]
        var result = validate(failure, .identity)
        precondition(result.0?.ok == false && result.0?.euid == 501 && result.1 == nil,
                     "a real failed persona transition must preserve observed IDs")
        failure["operation"] = "prepare.existing-file"
        result = validate(failure, .prepare)
        precondition(result.0?.ok == false && result.1 == nil)
        result = validate(failure, .check)
        precondition(result.0 == nil && result.1 != nil, "a failure from another operation was accepted")
        failure.removeValue(forKey: "errno")
        result = validate(failure, .prepare)
        precondition(result.0 == nil && result.1 != nil, "failure without errno was accepted")

        for invalidData in [Data(), Data("{}{}".utf8), Data(repeating: 32, count: 4096)] {
            result = RootHelperProtocol.validate(invalidData, operation: .identity, commit: commit)
            precondition(result.0 == nil && result.1 != nil, "invalid bounded JSON was accepted")
        }
        print("PASS root helper protocol: four operation reports, matching build, observed non-root IDs, malformed/oversized JSON, operation/metadata/error validation")
    }
}
