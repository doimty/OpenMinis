import Foundation

/// Executes the production resolver, not a duplicate implementation. Catalog
/// availability is an explicit test double; it is not a claim of an iOS 15 run.
@main
struct SFSymbolFallbackTests {
    static func main() throws {
        let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
        let json = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        let catalog = json["introduced_ios"] as! [String: String]
        func version(_ value: String) -> Int {
            let parts = value.split(separator: ".").compactMap { Int($0) }
            return parts[0] * 100 + (parts.count > 1 ? parts[1] : 0)
        }
        var checked = 0
        func check(_ condition: Bool, _ message: String) {
            guard condition else { fatalError("FAIL: \(message)") }
            checked += 1
        }
        let legacy = Set(catalog.filter { version($0.value) <= 1500 }.map(\.key))
        let required: [String: String] = [
            "mic.and.signal.meter": "mic",
            "waveform.badge.mic": "mic",
            "waveform.slash": "mic.slash",
            "arrow.trianglehead.2.counterclockwise": "arrow.triangle.2.circlepath",
            "arrow.triangle.2.circlepath.icloud": "arrow.triangle.2.circlepath",
            "person.badge.shield.checkmark": "person.crop.circle.badge.checkmark",
            "photo.badge.plus": "photo",
            "document.on.clipboard": "doc.on.clipboard",
            "key.circle.fill": "key.fill",
        ]
        for (requested, expected) in required {
            check(!legacy.contains(requested), "negative control must be absent at 15.0: \(requested)")
            check(CompatSystemSymbol.resolve(requested, isAvailable: legacy.contains) == expected,
                  "semantic legacy fallback for \(requested)")
        }
        for (requested, fallback) in CompatSystemSymbol.fallbacks {
            check(catalog[requested] != nil, "unknown requested-name metadata: \(requested)")
            check(legacy.contains(fallback), "fallback too new or invalid: \(fallback)")
            check(CompatSystemSymbol.resolve(requested, isAvailable: { $0 == requested }) == requested,
                  "an available modern symbol must not be replaced: \(requested)")
        }
        for floor in [1500, 1501, 1600, 1700, 1800, 2600] {
            let available = Set(catalog.filter { version($0.value) <= floor }.map(\.key))
            for requested in catalog.keys {
                let resolved = CompatSystemSymbol.resolve(requested, isAvailable: available.contains)
                check(available.contains(resolved), "unrenderable result at \(floor): \(requested) -> \(resolved)")
                if available.contains(requested) {
                    check(resolved == requested, "supported name changed at \(floor): \(requested)")
                }
            }
        }
        for invalid in ["", "user.supplied.unknown.symbol", "future.symbol.not.in.catalog"] {
            check(CompatSystemSymbol.resolve(invalid, isAvailable: legacy.contains) == "questionmark.circle",
                  "unknown name must have a visible legacy fallback")
        }
        // An explicit semantic fallback can itself be absent on a strange
        // catalog. Do not return a known-invalid second choice.
        check(CompatSystemSymbol.resolve("mic.and.signal.meter", isAvailable: { $0 == "questionmark.circle" }) == "questionmark.circle",
              "unavailable mapped fallback must use the safe final glyph")
        print("PASS: \(checked) production-resolver checks across six availability catalogs; not an iOS runtime test")
    }
}
