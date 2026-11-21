#if MINIS_ROOT_MANAGEMENT
import CoreFoundation
import Dispatch
import Foundation

struct RootManagementBuildIdentity: Codable, Sendable {
    let schema: Int
    let commit: String
    let actionsRunID: String

    init?(info: [String: Any]) {
        guard info["MinisRootManagementEnabled"] as? Bool == true,
              let schema = info["MinisRootManagementSchema"] as? NSNumber,
              CFGetTypeID(schema) != CFBooleanGetTypeID(), schema.intValue == 1,
              let commit = info["MinisRootBuildCommit"] as? String,
              commit.count == 40, commit.utf8.allSatisfy({ (48...57).contains($0) || (97...102).contains($0) }),
              let run = info["MinisRootActionsRunID"] as? String,
              !run.isEmpty, run.utf8.allSatisfy({ (48...57).contains($0) }),
              run.first != "0"
        else { return nil }
        self.schema = schema.intValue
        self.commit = commit
        actionsRunID = run
    }
}

struct RootClientReport: Codable, Sendable {
    let stage: String
    let errorNumber: Int
    let exitCode: Int
    let terminationSignal: Int
    let timedOut: Bool
    let outputTruncated: Bool
    let outputBytes: Int
    let appUID: UInt32
    let appEUID: UInt32
}

struct RootManagementReport: Encodable, Sendable {
    let schema = 1
    let build: RootManagementBuildIdentity
    let osVersion: String
    let bundleIdentifier: String
    let recordedAt: String
    let requestedOperation: RootManagementOperation
    let transport: RootClientReport
    let helper: RootHelperReport?
    let validationError: String?

    var rootObserved: Bool {
        validationError == nil && transport.errorNumber == 0 && transport.exitCode == 0 &&
        transport.terminationSignal == 0 && !transport.timedOut && !transport.outputTruncated &&
        helper?.ok == true && helper?.uid == 0 && helper?.euid == 0 &&
        helper?.operation == requestedOperation.rawValue
    }

    var exportJSON: String? {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(self) else { return nil }
        return String(data: data, encoding: .utf8)
    }
}

enum RootManagementRunner {
    private static let queue = DispatchQueue(label: "com.openminis.root-management", qos: .userInitiated)

    static func run(_ operation: RootManagementOperation, build: RootManagementBuildIdentity,
                    os: String, bundle: String) async -> RootManagementReport {
        await withCheckedContinuation { continuation in
            queue.async {
                continuation.resume(returning: runOnWorker(operation, build: build, os: os, bundle: bundle))
            }
        }
    }

    private static func runOnWorker(_ operation: RootManagementOperation, build: RootManagementBuildIdentity,
                                    os: String, bundle: String) -> RootManagementReport {
        let nativeOperation: OMRootOperation
        switch operation {
        case .identity: nativeOperation = OMRootOperationIdentity
        case .prepare: nativeOperation = OMRootOperationPrepare
        case .check: nativeOperation = OMRootOperationCheck
        case .cleanup: nativeOperation = OMRootOperationCleanup
        }
        var buffer = [CChar](repeating: 0, count: 4096)
        let native = buffer.withUnsafeMutableBufferPointer {
            OMRootManagementRun(nativeOperation, $0.baseAddress, $0.count)
        }
        let transport = RootClientReport(
            stage: native.stage.map { String(cString: $0) } ?? "client.unknown",
            errorNumber: Int(native.error_number), exitCode: Int(native.exit_code),
            terminationSignal: Int(native.termination_signal), timedOut: native.timed_out != 0,
            outputTruncated: native.output_truncated != 0, outputBytes: Int(native.output_bytes),
            appUID: native.caller_uid, appEUID: native.caller_euid)
        let data = Data(buffer.prefix(min(Int(native.output_bytes), buffer.count - 1)).map { UInt8(bitPattern: $0) })
        var helper: RootHelperReport?
        var validationError: String?
        if transport.errorNumber == 0 && !transport.outputTruncated {
            (helper, validationError) = RootHelperProtocol.validate(data, operation: operation, commit: build.commit)
        } else {
            validationError = "The client did not obtain a complete helper report."
        }
        return RootManagementReport(build: build, osVersion: os, bundleIdentifier: bundle,
                                    recordedAt: ISO8601DateFormatter().string(from: Date()),
                                    requestedOperation: operation, transport: transport,
                                    helper: helper, validationError: validationError)
    }
}
#endif
