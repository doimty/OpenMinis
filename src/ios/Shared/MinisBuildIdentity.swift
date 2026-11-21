import Foundation

enum MinisBuildIdentity: CaseIterable, Hashable, Sendable {
    case standard
    case broadFiles
    case root

    static let current = resolve(bundleIdentifier: Bundle.main.bundleIdentifier)

    static func resolve(bundleIdentifier: String?) -> MinisBuildIdentity {
        guard let bundleIdentifier else { return .standard }
        for identity in [MinisBuildIdentity.root, .broadFiles] {
            let base = identity.bundleIdentifier
            if bundleIdentifier == base || bundleIdentifier.hasPrefix(base + ".") {
                return identity
            }
        }
        return .standard
    }

    var bundleIdentifier: String {
        switch self {
        case .standard: return "com.openminis.app"
        case .broadFiles: return "com.openminis.app.broadfiles"
        case .root: return "com.openminis.app.root"
        }
    }

    var appGroupID: String { "group." + bundleIdentifier }

    var fileProviderDomainIdentifier: String { bundleIdentifier + ".files" }

    var deepLinkScheme: String {
        switch self {
        case .standard: return "minis"
        case .broadFiles: return "minis-broadfiles"
        case .root: return "minis-root"
        }
    }

    var displayName: String {
        switch self {
        case .standard: return "Minis"
        case .broadFiles: return "Minis Files"
        case .root: return "Minis Root"
        }
    }
}
