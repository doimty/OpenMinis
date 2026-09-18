import XCTest
@testable import Minis

final class LegacyFlowLayoutTests: XCTestCase {
    func testEmptyFlowHasNoHeight() {
        let result = LegacyFlowArrangement.pack(sizes: [], width: 200, hSpacing: 6, vSpacing: 6, trailing: true)
        XCTAssertEqual(result.positions, [])
        XCTAssertEqual(result.height, 0)
    }

    func testTrailingPartialRowMatchesAttachmentTileMetrics() {
        let result = LegacyFlowArrangement.pack(
            sizes: Array(repeating: CGSize(width: 64, height: 64), count: 3),
            width: 134, hSpacing: 6, vSpacing: 6, trailing: true)
        XCTAssertEqual(result.positions, [CGPoint(x: 0, y: 0), CGPoint(x: 70, y: 0), CGPoint(x: 70, y: 70)])
        XCTAssertEqual(result.height, 134)
    }

    func testRowHeightUsesTallestItem() {
        let result = LegacyFlowArrangement.pack(
            sizes: [CGSize(width: 40, height: 10), CGSize(width: 40, height: 30), CGSize(width: 40, height: 20)],
            width: 90, hSpacing: 8, vSpacing: 8, trailing: false)
        XCTAssertEqual(result.positions, [CGPoint(x: 0, y: 0), CGPoint(x: 48, y: 0), CGPoint(x: 0, y: 38)])
        XCTAssertEqual(result.height, 58)
    }

    func testOversizeFirstItemDoesNotCreateAnEmptyRow() {
        let result = LegacyFlowArrangement.pack(sizes: [CGSize(width: 120, height: 40)],
                                               width: 100, hSpacing: 8, vSpacing: 8, trailing: false)
        XCTAssertEqual(result.positions, [CGPoint.zero])
        XCTAssertEqual(result.height, 40)
    }
}
