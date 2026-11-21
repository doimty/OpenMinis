#if MINIS_ROOT_MANAGEMENT && os(iOS)
import SwiftUI
import UIKit
import Darwin

private struct RootManagementBuildIdentity: Codable, Sendable {
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

private struct RootClientReport: Codable, Sendable {
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

private struct RootManagementReport: Encodable, Sendable {
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

struct RootManagementView: View {
    @Environment(\.scenePhase) private var scenePhase
    @State private var authorization = RootManagementAuthorization()
    @State private var report: RootManagementReport?
    @State private var confirmation: RootManagementOperation?

    private var buildIdentity: RootManagementBuildIdentity? {
        RootManagementBuildIdentity(info: Bundle.main.infoDictionary ?? [:])
    }

    var body: some View {
        List {
            Section {
                Text("Runs a bundled helper for one explicitly confirmed operation. The available operations inspect identity and create, read back or remove only a fixed diagnostic sample. Each operation needs a new confirmation.")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            }

            Section("Build") {
                row("Bundle", Bundle.main.bundleIdentifier ?? "unknown")
                row("iOS", ProcessInfo.processInfo.operatingSystemVersionString)
                if let identity = buildIdentity {
                    row("Source commit", identity.commit)
                    row("Actions run", identity.actionsRunID)
                } else {
                    Text("Root build identity is missing or invalid. Operations are disabled.")
                        .foregroundStyle(.orange)
                }
            }

            Section {
                Toggle("Enable for this visit", isOn: Binding(
                    get: { authorization.sessionEnabled },
                    set: { authorization.setEnabled($0) }
                ))
                .disabled(buildIdentity == nil || authorization.running != nil)
                ForEach(RootManagementOperation.allCases) { operation in
                    Button(operation.title) {
                        if authorization.request(operation) { confirmation = operation }
                    }
                        .disabled(buildIdentity == nil || !authorization.sessionEnabled ||
                                  authorization.pending != nil || authorization.running != nil)
                }
                if let running = authorization.running {
                    HStack {
                        ProgressView()
                        Text(running.title)
                            .font(.system(size: 12))
                    }
                }
            } header: {
                Text("Restricted operations")
            } footer: {
                Text("The grant is kept only in this view. Leaving the page or backgrounding revokes unused permission. A started operation may already have changed the fixed sample.")
            }

            if let report {
                Section("Last operation") {
                    row("Requested", report.requestedOperation.rawValue)
                    row("App UID · EUID", "\(report.transport.appUID) · \(report.transport.appEUID)")
                    row("Client stage", report.transport.stage)
                    row("Client errno", "\(report.transport.errorNumber)")
                    row("Exit · signal", "\(report.transport.exitCode) · \(report.transport.terminationSignal)")
                    if let helper = report.helper {
                        row("Helper UID · EUID", "\(helper.uid) · \(helper.euid)")
                        row("Helper stage", helper.operation)
                        if let value = helper.errorNumber { row("Helper errno", "\(value)") }
                        if let marker = helper.markerID { row("Sample ID", marker) }
                        if let inode = helper.inode { row("File inode", inode) }
                        if let bytes = helper.bytes { row("File bytes", "\(bytes)") }
                    }
                    if let error = report.validationError {
                        Text(error).foregroundStyle(.orange)
                    }
                    Text(report.rootObserved
                         ? "The matching bundled helper reported UID 0 and EUID 0 for this operation."
                         : "Root execution has not been verified by this result.")
                        .font(.system(size: 12))
                        .foregroundStyle(report.rootObserved ? .primary : .secondary)
                    if report.transport.timedOut || report.transport.terminationSignal != 0 {
                        Text("The operation did not complete normally. Its filesystem outcome is unknown; inspect the fixed sample before retrying.")
                            .font(.system(size: 12))
                            .foregroundStyle(.orange)
                    }
                    Button("Copy scalar report") {
                        if let json = report.exportJSON { UIPasteboard.general.string = json }
                    }
                }
            }
        }
        .navigationTitle("Root Management")
        .navigationBarTitleDisplayMode(.inline)
        // SwiftUI clears the presentation binding during dismissal. Keep it
        // separate from the grant so that this cannot consume/cancel the
        // pending operation before the primary button's action runs.
        .alert(item: $confirmation) { operation in
            Alert(title: Text("Allow once: \(operation.title)?"),
                  message: Text(operation.explanation),
                  primaryButton: .default(Text("Allow once")) { execute(operation) },
                  secondaryButton: .cancel { authorization.cancel() })
        }
        .onChange(of: scenePhase) { phase in
            if phase != .active { authorization.revoke(); confirmation = nil }
        }
        .onDisappear { authorization.revoke(); confirmation = nil }
    }

    private func row(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).font(.system(size: 13))
            Spacer(minLength: 8)
            Text(value)
                .font(.system(size: 12, design: .monospaced))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
    }

    private func execute(_ operation: RootManagementOperation) {
        guard let identity = buildIdentity, authorization.consume(operation) else { return }
        let os = ProcessInfo.processInfo.operatingSystemVersionString
        let bundle = Bundle.main.bundleIdentifier ?? "unknown"
        Task.detached(priority: .userInitiated) {
            let result = Self.run(operation, build: identity, os: os, bundle: bundle)
            await MainActor.run {
                report = result
                authorization.finish()
            }
        }
    }

    private static func run(_ operation: RootManagementOperation, build: RootManagementBuildIdentity,
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
