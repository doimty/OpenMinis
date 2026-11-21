import Foundation

@main
enum MinisBuildIdentityTests {
    static func main() {
        for identity in MinisBuildIdentity.allCases {
            for suffix in ["", ".ShareExtension", ".FileProvider", ".AgentWidget"] {
                let resolved = MinisBuildIdentity.resolve(bundleIdentifier: identity.bundleIdentifier + suffix)
                precondition(resolved == identity)
                precondition(resolved.appGroupID == identity.appGroupID)
                precondition(resolved.fileProviderDomainIdentifier == identity.fileProviderDomainIdentifier)
                precondition(resolved.deepLinkScheme == identity.deepLinkScheme)
                precondition(resolved.displayName == identity.displayName)
            }
        }
        for identifier in [nil, "", "com.openminis.app.rooted", "com.openminis.app.root2",
                           "com.openminis.app.rootFileProvider", "com.openminis.app.broadfiles2",
                           "com.openminis.app.broadfilesWidget", "com.openminis.app.Root"] {
            precondition(MinisBuildIdentity.resolve(bundleIdentifier: identifier) == .standard)
        }
        precondition(MinisBuildIdentity.standard.appGroupID == "group.com.openminis.app")
        precondition(MinisBuildIdentity.standard.fileProviderDomainIdentifier == "com.openminis.app.files")
        precondition(MinisBuildIdentity.standard.deepLinkScheme == "minis")
        precondition(MinisBuildIdentity.standard.displayName == "Minis")
        precondition(MinisBuildIdentity.root.appGroupID == "group.com.openminis.app.root")
        precondition(MinisBuildIdentity.root.fileProviderDomainIdentifier == "com.openminis.app.root.files")
        precondition(MinisBuildIdentity.root.deepLinkScheme == "minis-root")
        precondition(MinisBuildIdentity.root.displayName == "Minis Root")
        precondition(MinisBuildIdentity.broadFiles.appGroupID == "group.com.openminis.app.broadfiles")
        precondition(MinisBuildIdentity.broadFiles.fileProviderDomainIdentifier == "com.openminis.app.broadfiles.files")
        precondition(MinisBuildIdentity.broadFiles.deepLinkScheme == "minis-broadfiles")
        precondition(MinisBuildIdentity.broadFiles.displayName == "Minis Files")
        precondition(Set(MinisBuildIdentity.allCases.map(\.appGroupID)).count == 3)
        precondition(Set(MinisBuildIdentity.allCases.map(\.fileProviderDomainIdentifier)).count == 3)
        precondition(Set(MinisBuildIdentity.allCases.map(\.deepLinkScheme)).count == 3)
        print("MinisBuildIdentity runtime mapping and component boundaries: PASS")
    }
}
