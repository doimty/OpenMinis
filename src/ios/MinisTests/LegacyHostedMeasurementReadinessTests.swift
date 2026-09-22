import XCTest
@testable import Minis

@MainActor
final class LegacyHostedMeasurementReadinessTests: XCTestCase {
    func testMarkdownTextViewIsNotReadyBeforeFiniteMeasurement() {
        let textView = SelectableMarkdownTextView()
        textView.attributedText = NSAttributedString(
            string: String(repeating: "finite width markdown ", count: 12),
            attributes: [.font: UIFont.systemFont(ofSize: 16)])

        XCTAssertFalse(textView.legacyHostedMeasurementReady)

        let width = min(max(UIScreen.main.bounds.width - 24, 120), 300)
        textView.frame = CGRect(x: 0, y: 0, width: width, height: 1)
        textView.textContainer.size = CGSize(width: width, height: .greatestFiniteMagnitude)
        _ = textView.sizeThatFits(CGSize(width: width, height: .greatestFiniteMagnitude))

        XCTAssertTrue(textView.legacyHostedMeasurementReady)
    }

    func testFiniteMeasurementBecomesStaleAfterWidthChanges() {
        let textView = SelectableMarkdownTextView()
        textView.attributedText = NSAttributedString(
            string: String(repeating: "width-sensitive markdown ", count: 8),
            attributes: [.font: UIFont.systemFont(ofSize: 16)])

        let firstWidth = min(max(UIScreen.main.bounds.width - 40, 120), 260)
        textView.frame = CGRect(x: 0, y: 0, width: firstWidth, height: 1)
        textView.textContainer.size = CGSize(width: firstWidth, height: .greatestFiniteMagnitude)
        _ = textView.sizeThatFits(CGSize(width: firstWidth, height: .greatestFiniteMagnitude))
        XCTAssertTrue(textView.legacyHostedMeasurementReady)

        let secondWidth = firstWidth - 20
        textView.frame.size.width = secondWidth
        textView.textContainer.size.width = secondWidth
        XCTAssertFalse(textView.legacyHostedMeasurementReady)

        _ = textView.sizeThatFits(CGSize(width: secondWidth, height: .greatestFiniteMagnitude))
        XCTAssertTrue(textView.legacyHostedMeasurementReady)
    }
}
