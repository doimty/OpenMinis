#if MINIS_ROOT_MANAGEMENT
import Foundation

enum RootManagementOperation: String, CaseIterable, Identifiable, Codable, Sendable {
    case identity, prepare, check, cleanup
    var id: String { rawValue }

    var title: String {
        switch self {
        case .identity: return "Read helper identity"
        case .prepare: return "Create fixed root sample"
        case .check: return "Read back fixed root sample"
        case .cleanup: return "Remove fixed root sample"
        }
    }

    var explanation: String {
        switch self {
        case .identity: return "Run the bundled helper once to report its UID and EUID. It does not open or change the sample."
        case .prepare: return "Create only the known sample in /private/var/mobile/Library/OpenMinisRootProbe. An existing file with different content or permissions is refused."
        case .check: return "Read only the known sample and compare its exact marker, ownership and permissions. No file content is shown."
        case .cleanup: return "Verify and remove only the known sample. The empty root-owned directory is kept."
        }
    }
}

/// No persistent grant. A confirmation consumes one pending operation before
/// execution begins. Backgrounding and dismissal revoke every unused grant.
struct RootManagementAuthorization {
    private(set) var sessionEnabled = false
    private(set) var pending: RootManagementOperation?
    private(set) var running: RootManagementOperation?

    mutating func setEnabled(_ value: Bool) {
        sessionEnabled = value
        pending = nil
    }

    @discardableResult
    mutating func request(_ operation: RootManagementOperation) -> Bool {
        guard sessionEnabled, pending == nil, running == nil else { return false }
        pending = operation
        return true
    }

    mutating func cancel() { pending = nil }

    mutating func consume(_ operation: RootManagementOperation) -> Bool {
        guard sessionEnabled, pending == operation, running == nil else { return false }
        pending = nil
        running = operation
        return true
    }

    mutating func finish() { running = nil }

    mutating func revoke() {
        sessionEnabled = false
        pending = nil
        // An operation already launched has its own bounded completion. This
        // revokes unused permission; it does not claim to undo completed I/O.
    }
}

struct RootHelperReport: Codable, Sendable {
    let schema: Int
    let helperCommit: String
    let ok: Bool
    let operation: String
    let uid: UInt32
    let euid: UInt32
    let errorNumber: Int?
    let markerID: String?
    let device: String?
    let inode: String?
    let bytes: UInt64?
    let alreadyPrepared: Bool?
    let removed: Bool?

    enum CodingKeys: String, CodingKey {
        case schema, ok, operation, uid, euid, device, inode, bytes, removed
        case helperCommit = "helper_commit"
        case errorNumber = "errno"
        case markerID = "marker_id"
        case alreadyPrepared = "already_prepared"
    }
}

/// Foundation-only so Actions can run malformed-response tests against the
/// same validator used by the iOS view, without launching a helper process.
enum RootHelperProtocol {
    static let markerID = "fb81bca8-f8c0-4f2e-9ff2-fbfaf351aa87"
    static let sampleBytes = UInt64(("OPENMINIS-ROOT-PROBE-V1:" + markerID + "\n").utf8.count)

    static func validate(_ data: Data, operation: RootManagementOperation,
                         commit: String) -> (RootHelperReport?, String?) {
        let allowedKeys: Set<String> = ["schema", "helper_commit", "ok", "operation", "uid", "euid",
                                        "errno", "marker_id", "device", "inode", "bytes", "already_prepared", "removed"]
        guard !data.isEmpty, data.count <= 4095,
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys).isSubset(of: allowedKeys),
              object.values.allSatisfy({ $0 is String || $0 is NSNumber }),
              let decoded = try? JSONDecoder().decode(RootHelperReport.self, from: data)
        else { return (nil, "The helper response was not the bounded scalar protocol.") }
        guard decoded.schema == 1, decoded.helperCommit == commit else {
            return (nil, "The helper schema or source commit does not match this installed app.")
        }
        let sharedFailureStages: Set<String> = ["not-root", "drop-real-ids", "invalid-arguments",
            "operation-not-allowed", "watchdog", "open-parent"]
        let operationFailureStages: Set<String>
        switch operation {
        case .identity:
            operationFailureStages = []
        case .prepare:
            operationFailureStages = ["prepare", "prepare.open-directory", "prepare.directory-policy",
                "prepare.existing-file", "prepare.create-file", "prepare.write"]
        case .check:
            operationFailureStages = ["check.open-directory", "check.directory-policy", "check.read"]
        case .cleanup:
            operationFailureStages = ["cleanup.open-directory", "cleanup.directory-policy",
                "cleanup.verify", "cleanup.unlink"]
        }
        guard decoded.operation == operation.rawValue ||
              (!decoded.ok && (sharedFailureStages.contains(decoded.operation) ||
                               operationFailureStages.contains(decoded.operation))) else {
            return (nil, "The helper response did not correspond to the requested operation.")
        }
        if let marker = decoded.markerID, marker != markerID {
            return (nil, "The helper returned an unexpected sample identity.")
        }
        for value in [decoded.device, decoded.inode].compactMap({ $0 }) {
            guard !value.isEmpty, value.count <= 20,
                  value.utf8.allSatisfy({ (48...57).contains($0) }) else {
                return (nil, "The helper returned invalid file identity metadata.")
            }
        }
        if decoded.ok {
            guard decoded.uid == 0, decoded.euid == 0,
                  decoded.errorNumber == nil || decoded.errorNumber == 0 else {
                return (nil, "The helper success response did not report root identity and a zero error.")
            }
            switch operation {
            case .identity:
                break
            case .prepare:
                guard decoded.markerID == markerID else {
                    return (nil, "The helper did not confirm the fixed sample identity.")
                }
            case .check:
                guard decoded.markerID == markerID, decoded.device != nil, decoded.inode != nil,
                      decoded.bytes == sampleBytes else {
                    return (nil, "The helper did not confirm the complete fixed sample metadata.")
                }
            case .cleanup:
                guard decoded.removed == true else {
                    return (nil, "The helper did not confirm removal of the fixed sample.")
                }
            }
        } else if (decoded.errorNumber ?? 0) <= 0 {
            return (nil, "The helper failure response did not include a positive error number.")
        }
        return (decoded, nil)
    }
}
#endif
