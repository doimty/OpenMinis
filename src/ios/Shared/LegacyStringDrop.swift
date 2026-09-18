import Foundation
import UniformTypeIdentifiers

/// String payload bridging for the sidebar drag/drop on iOS 15.
///
/// iOS 16's `draggable`/`dropDestination(for:)` pair is not available, so the
/// sidebar uses the older `onDrag`/`onDrop` APIs with NSString payloads. This
/// type keeps the payload format and the asynchronous, ordered, best-effort
/// semantics of the native drop destination.
enum LegacyStringDrop {

    /// A provider that is worth asking for a String. Rejects non-text types.
    static func readableProviders(from providers: [NSItemProvider]) -> [NSItemProvider] {
        providers.filter { $0.hasItemConformingToTypeIdentifier(UTType.text.identifier) }
    }

    /// Load each readable provider's String, in order, ignoring failed
    /// providers. Cancellation discards the batch. One result per provider;
    /// a successfully loaded empty string is preserved.
    static func loadValues(from providers: [NSItemProvider]) async -> [String] {
        var values: [String] = []
        for provider in readableProviders(from: providers) {
            guard !Task.isCancelled else { return [] }
            if let value = await loadValue(from: provider) {
                values.append(value)
            }
        }
        return Task.isCancelled ? [] : values
    }

    private static func loadValue(from provider: NSItemProvider) async -> String? {
        await withCheckedContinuation { (continuation: CheckedContinuation<String?, Never>) in
            provider.loadItem(forTypeIdentifier: UTType.text.identifier, options: nil) { item, _ in
                switch item {
                case let string as String:
                    continuation.resume(returning: string)
                case let data as Data:
                    continuation.resume(returning: String(data: data, encoding: .utf8))
                default:
                    continuation.resume(returning: nil)
                }
            }
        }
    }
}
