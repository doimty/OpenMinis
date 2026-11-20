import Foundation

/// Reads and writes PendingShare data to the App Group shared container.
/// Compiled into both the main app target and the Share Extension target.
enum SharedContainerStore {
    // The opt-in TrollStore file-access IPA is repackaged with its own bundle
    // identifier. Resolve this at runtime so its share extension, rootfs and
    // global stores never attach to the ordinary build's App Group.
    static let isBroadFilesBuild: Bool = {
        let id = Bundle.main.bundleIdentifier ?? ""
        let base = "com.openminis.app.broadfiles"
        return id == base || id.hasPrefix(base + ".")
    }()
    static let appGroupID = isBroadFilesBuild
        ? "group.com.openminis.app.broadfiles" : "group.com.openminis.app"
    static let deepLinkScheme = isBroadFilesBuild ? "minis-broadfiles" : "minis"

    /// Shared containers may be unavailable after an installer re-signs the
    /// experimental IPA. Keep main-app storage usable without falling back to
    /// the ordinary build's group; extension handoff still needs the real group.
    static var storageContainerURL: URL {
        let fm = FileManager.default
        return fm.containerURL(forSecurityApplicationGroupIdentifier: appGroupID)
            ?? fm.urls(for: .libraryDirectory, in: .userDomainMask)[0]
                .appendingPathComponent("MinisLocalGroup", isDirectory: true)
    }

    private static let pendingShareKey = "pendingShare"

    static var sharedDefaults: UserDefaults? {
        UserDefaults(suiteName: appGroupID)
    }

    /// Directory in the shared container for transferring attachment files.
    static var sharedFileDirectory: URL? {
        FileManager.default
            .containerURL(forSecurityApplicationGroupIdentifier: appGroupID)?
            .appendingPathComponent("ShareExtension", isDirectory: true)
    }

    // MARK: - Write (called by Share Extension)

    static func savePendingShare(_ share: PendingShare) {
        guard let defaults = sharedDefaults else { return }
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        if let data = try? encoder.encode(share) {
            defaults.set(data, forKey: pendingShareKey)
            defaults.synchronize()
        }
    }

    // MARK: - Read & Consume (called by main app)

    static func loadPendingShare() -> PendingShare? {
        guard let defaults = sharedDefaults,
              let data = defaults.data(forKey: pendingShareKey) else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(PendingShare.self, from: data)
    }

    static func clearPendingShare() {
        sharedDefaults?.removeObject(forKey: pendingShareKey)
        sharedDefaults?.synchronize()
    }

    /// Remove all files from the shared transfer directory.
    static func cleanSharedFiles() {
        guard let dir = sharedFileDirectory else { return }
        let fm = FileManager.default
        if let files = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil) {
            for file in files {
                try? fm.removeItem(at: file)
            }
        }
    }
}
