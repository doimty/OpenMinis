//
//  HostFileAccessProbeView.swift
//  Minis
//
//  [T-host-file-probe] A deliberately small settings page that answers one
//  question with evidence: can THIS binary's native process read a file
//  outside its own container?
//
//  It is not a file browser and it takes no path argument. The only file it
//  ever reads is the fixed, documented probe sample, whose exact bytes are
//  compiled into the app — so an unlocked read and a "read succeeded but the
//  content was not the sample" can be told apart in the report. Nothing from
//  the file is displayed; only the outcome, a stage label and real errno.
//
//  Everything here exists only when a build explicitly opts in with
//  MINIS_HOST_FILE_PROBE; ordinary builds compile the page away entirely.
//

#if DEBUG && MINIS_HOST_FILE_PROBE && os(iOS)
import SwiftUI
import Darwin

struct HostFileAccessProbeView: View {
    private struct Report: Equatable {
        var controlOK = false
        var outcome = -1
        var errorNumber = 0
        var stage = ""
        var sampleId = ""
        var device: UInt64 = 0
        var inode: UInt64 = 0
        var size: UInt64 = 0
        var controlStage = ""
        var controlErrno = 0
        var uid: UInt32 = 0
        var euid: UInt32 = 0
    }

    @State private var report: Report?
    @State private var running = false

    private static let outcomeNames: [Int: String] = [
        Int(OMHostProbeMatch): "Read returned the expected sample",
        Int(OMHostProbeSystemError): "System call failed",
        Int(OMHostProbeNotRegular): "Not a regular file",
        Int(OMHostProbeUnexpectedSize): "Unexpected size",
        Int(OMHostProbeInvalidMarker): "Content is not the probe sample",
        Int(OMHostProbeFileChanged): "File changed while reading",
        Int(OMHostProbeMultipleLinks): "File is hard linked",
    ]

    var body: some View {
        List {
            Section {
                Text("Reads one fixed diagnostic sample outside this app's container, and reports what the operating system answered. It shows no file content, takes no path, writes nothing and is never called automatically.")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
            } header: {
                Text("What this does")
            }

            Section("Target") {
                row("Sample path", String(cString: OMHostFileProbeSamplePath()), mono: true)
                row("Bundle identifier", Bundle.main.bundleIdentifier ?? "unknown", mono: true)
                row("Process", report.map { "uid \($0.uid) · euid \($0.euid)" } ?? "not read yet", mono: true)
            }

            Section {
                Button {
                    guard !running else { return }
                    running = true
                    Task.detached(priority: .userInitiated) {
                        let result = Self.measure()
                        await MainActor.run {
                            report = result
                            running = false
                        }
                    }
                } label: {
                    HStack {
                        Text(running ? "Reading…" : "Read probe sample")
                        Spacer()
                        if running { ProgressView().scaleEffect(0.8) }
                    }
                }
                .disabled(running)
            } footer: {
                Text("Runs once per tap, in this process, on a background thread. Nothing repeats without another tap.")
            }

            if let report {
                Section("Control") {
                    row("Own Info.plist read", report.controlOK ? "succeeded" : "failed", mono: false)
                    if !report.controlOK {
                        row("Control stage", report.controlStage, mono: true)
                        row("Control errno", "\(report.controlErrno)", mono: true)
                        Text("A failed control means this process cannot read even its own bundle. The sample result below is not evidence either way.")
                            .font(.system(size: 11))
                            .foregroundStyle(.orange)
                    }
                }

                Section("Result") {
                    row("Outcome", Self.outcomeNames[report.outcome] ?? "code \(report.outcome)", mono: false)
                    row("Stage", report.stage, mono: true)
                    if report.errorNumber != 0 {
                        row("Errno", "\(report.errorNumber) (\(Self.errnoName(report.errorNumber)))", mono: true)
                        Text(Self.errnoHint(report.errorNumber))
                            .font(.system(size: 11))
                            .foregroundStyle(.secondary)
                    }
                    if !report.sampleId.isEmpty {
                        row("Sample id", report.sampleId, mono: true)
                        row("Device · inode", "\(report.device) · \(report.inode)", mono: true)
                        row("Size", "\(report.size) bytes", mono: true)
                    }
                }

                Section {
                    Text("A match means this fixed sample was readable. It does not mean arbitrary paths, other apps' data, protected files or root are reachable — each of those is a separate capability with its own check.")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle("Host File Access")
        .navigationBarTitleDisplayMode(.inline)
    }

    private func row(_ label: String, _ value: String, mono: Bool) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label)
                .font(.system(size: 13))
            Spacer(minLength: 8)
            Text(value)
                .font(.system(size: 12, design: mono ? .monospaced : .default))
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.trailing)
                .textSelection(.enabled)
        }
    }

    private static func errnoName(_ value: Int) -> String {
        switch value {
        case Int(EACCES): return "EACCES"
        case Int(EPERM): return "EPERM"
        case Int(ENOENT): return "ENOENT"
        case Int(ELOOP): return "ELOOP"
        case Int(ENOTDIR): return "ENOTDIR"
        case Int(EISDIR): return "EISDIR"
        case Int(EINVAL): return "EINVAL"
        default: return "code \(value)"
        }
    }

    private static func errnoHint(_ value: Int) -> String {
        switch value {
        case Int(EACCES), Int(EPERM):
            return "The sandbox or the file's own permissions denied this path. Same-process reads of other apps' data are not implied."
        case Int(ENOENT):
            return "The path does not exist — the branch that returned it may be hidden by the sandbox rather than genuinely absent. This is not a permission answer."
        case Int(ELOOP):
            return "A component of the path is a symbolic link; the probe refuses to follow one by design."
        default:
            return "Reported verbatim so the answer can be looked up instead of guessed."
        }
    }

    /// Reads the sample, plus a bounded read of our own bundle as a control.
    private static func measure() -> Report {
        var out = Report()
        out.uid = getuid()
        out.euid = geteuid()

        let control = Self.readOwnInfoPlist()
        out.controlOK = control.0
        out.controlStage = control.1
        out.controlErrno = control.2

        let result = OMHostFileProbeReadSample()
        out.outcome = Int(result.outcome)
        out.errorNumber = Int(result.error_number)
        out.stage = result.stage.map { String(cString: $0) } ?? ""
        out.sampleId = result.sample_id.map { String(cString: $0) } ?? ""
        out.device = result.device
        out.inode = result.inode
        out.size = result.size
        return out
    }

    /// Bounded, read-only, no bytes kept: proves the reader path itself works.
    private static func readOwnInfoPlist() -> (Bool, String, Int) {
        guard let url = Bundle.main.url(forResource: "Info", withExtension: "plist") else {
            return (false, "bundle.locate", ENOENT)
        }
        let fd = open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC)
        if fd < 0 { return (false, "bundle.open", errno) }
        defer { close(fd) }
        var buffer = [UInt8](repeating: 0, count: 64)
        let count = read(fd, &buffer, buffer.count)
        if count < 0 { return (false, "bundle.read", errno) }
        return count > 0 ? (true, "bundle.read", 0) : (false, "bundle.empty", 0)
    }
}
#endif