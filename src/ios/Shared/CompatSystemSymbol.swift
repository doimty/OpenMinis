import Foundation
#if canImport(UIKit)
import UIKit
#endif

/// Resolve glyph names at the rendering boundary, not in persisted models.
/// SF Symbol names are strings, so lowering the deployment target does not
/// catch names absent on an older OS. Keep native Image/Label/UIImage callers
/// (and their localization, configuration and rendering modes) unchanged.
enum CompatSystemSymbol {
    /// Known app symbols introduced after iOS 15.0, with semantic old-system
    /// substitutes. Runtime availability wins over this table on newer OSes.
    static let fallbacks: [String: String] = [
        "apple.logo": "waveform",
        "arrow.triangle.2.circlepath.icloud": "arrow.triangle.2.circlepath",
        "arrow.trianglehead.2.counterclockwise": "arrow.triangle.2.circlepath",
        "bolt.badge.checkmark": "bolt.fill",
        "bubble.left.and.text.bubble.right": "bubble.left.and.bubble.right",
        "calendar.badge.checkmark": "calendar",
        "doc.badge.arrow.up": "arrow.up.doc",
        "document.on.clipboard": "doc.on.clipboard",
        "externaldrive.badge.questionmark": "externaldrive",
        "key.circle.fill": "key.fill",
        "key.slash": "key",
        "lightbulb.max": "lightbulb",
        "mic.and.signal.meter": "mic",
        "mountain.2": "triangle",
        "opticid": "faceid",
        "pencil.line": "pencil",
        "person.badge.shield.checkmark": "person.crop.circle.badge.checkmark",
        "photo.badge.plus": "photo",
        "waveform.badge.mic": "mic",
        "waveform.slash": "mic.slash",
    ]

    /// Pure seam used by the offline catalog regression matrix. Do not infer
    /// support solely from an OS version: symbol availability/aliases vary.
    static func resolve(_ requested: String, isAvailable: (String) -> Bool) -> String {
        if !requested.isEmpty, isAvailable(requested) {
            return requested
        }
        if let fallback = fallbacks[requested], isAvailable(fallback) {
            return fallback
        }
        // Available since iOS 13; unknown external/configured names should
        // be visibly unknown instead of producing a blank affordance.
        return "questionmark.circle"
    }

    #if canImport(UIKit)
    // NSCache is thread-safe and bounded. Repeated SwiftUI body evaluation
    // should not repeatedly probe (and log) an absent system symbol.
    private static let cache: NSCache<NSString, NSString> = {
        let cache = NSCache<NSString, NSString>()
        cache.countLimit = 256
        return cache
    }()

    static func name(_ requested: String) -> String {
        if let cached = cache.object(forKey: requested as NSString) {
            return cached as String
        }
        let resolved = resolve(requested) { UIImage(systemName: $0) != nil }
        cache.setObject(resolved as NSString, forKey: requested as NSString)
        return resolved
    }
    #endif
}
