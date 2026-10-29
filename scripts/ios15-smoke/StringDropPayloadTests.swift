import Foundation

@main
struct StringDropPayloadTests {
    static func main() async {
        let first = NSItemProvider(object: "session-a" as NSString)
        let second = NSItemProvider(object: "中文😀session-b" as NSString)
        let empty = NSItemProvider(object: "" as NSString)
        let unsupported = NSItemProvider(item: Data([0]) as NSData, typeIdentifier: "public.png")
        precondition(LegacyStringDrop.readableProviders(from: [unsupported]).isEmpty)
        let values = await LegacyStringDrop.loadValues(from: [first, unsupported, second, empty])
        precondition(values == ["session-a", "中文😀session-b", ""], "String drops must retain order and contents")

        let failed = NSItemProvider()
        failed.registerDataRepresentation(forTypeIdentifier: "public.utf8-plain-text", visibility: .all) { completion in
            completion(nil, NSError(domain: "StringDropFixture", code: 1))
            return nil
        }
        let partial = await LegacyStringDrop.loadValues(from: [failed, first])
        precondition(partial == ["session-a"], "Failed providers must not replace or duplicate successful values")
        let noValues = await LegacyStringDrop.loadValues(from: [unsupported, failed])
        precondition(noValues.isEmpty)

        let cancelled = Task {
            withUnsafeCurrentTask { $0?.cancel() }
            return await LegacyStringDrop.loadValues(from: [first])
        }
        let cancelledValues = await cancelled.value
        precondition(cancelledValues.isEmpty, "A cancelled load must not produce a move batch")
        print("PASS: production NSItemProvider String loading preserves order, Unicode, failure and cancellation")
    }
}
