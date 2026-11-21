#if MINIS_ROOT_MANAGEMENT && os(iOS)
import SwiftUI
import UIKit

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
                    Button(AppLocalizedDynamic(operation.title)) {
                        if authorization.request(operation) { confirmation = operation }
                    }
                        .disabled(buildIdentity == nil || !authorization.sessionEnabled ||
                                  authorization.pending != nil || authorization.running != nil)
                }
                if let running = authorization.running {
                    HStack {
                        ProgressView()
                        Text(AppLocalizedDynamic(running.title))
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
                    row("Requested operation", AppLocalizedDynamic(report.requestedOperation.title))
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
                        Text(AppLocalizedDynamic(error)).foregroundStyle(.orange)
                    }
                    Text(AppLocalizedDynamic(report.rootObserved
                         ? "The matching bundled helper reported UID 0 and EUID 0 for this operation."
                         : "Root execution has not been verified by this result."))
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
            Alert(title: Text(AppLocalized("Allow once: \(AppLocalizedDynamic(operation.title))?")),
                  message: Text(AppLocalizedDynamic(operation.explanation)),
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
            Text(AppLocalizedDynamic(label)).font(.system(size: 13))
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
        Task { @MainActor in
            report = await RootManagementRunner.run(operation, build: identity, os: os, bundle: bundle)
            authorization.finish()
        }
    }
}
#endif
