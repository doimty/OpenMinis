//
//  ChatPDFExporter.swift
//  MinisApp
//
//  Plain-text transcript export. Screenshot-based "long image" already exists
//  per turn; this produces a shareable document for the whole conversation
//  without scrolling the live collection view.
//

import UIKit
import CoreText

enum ChatPDFExporter {
    static let pageSize = CGSize(width: 595, height: 842) // A4 @72dpi
    static let margin: CGFloat = 44

    // MARK: - Snapshot

    /// Copy the conversation into plain sections on the main actor. Thinking
    /// blocks are dropped; tool blocks keep a one-line summary so the document
    /// shows what ran without turning into a log dump.
    @MainActor
    static func sections(from messages: [ChatMessage]) -> [ChatPDFSection] {
        var result: [ChatPDFSection] = []
        for message in messages {
            switch message.role {
            case .user:
                var text = message.content.trimmingCharacters(in: .whitespacesAndNewlines)
                if text.isEmpty { text = AppLocalized("[empty message]") }
                if !message.attachments.isEmpty {
                    text += "\n" + String(
                        format: AppLocalized("[%d attachment(s)]"),
                        message.attachments.count
                    )
                }
                result.append(ChatPDFSection(role: AppLocalized("You"), text: text))

            case .assistant:
                var parts: [String] = []
                for block in message.blocks {
                    switch block.kind {
                    case .thinking:
                        continue
                    case .text:
                        let text = block.content.trimmingCharacters(in: .whitespacesAndNewlines)
                        if !text.isEmpty { parts.append(text) }
                    case .info:
                        let text = block.content.trimmingCharacters(in: .whitespacesAndNewlines)
                        if !text.isEmpty { parts.append(text) }
                    default:
                        parts.append(toolLine(block))
                    }
                }
                if let error = message.error?.trimmingCharacters(in: .whitespacesAndNewlines),
                   !error.isEmpty {
                    parts.append("⚠︎ " + error)
                }
                if !parts.isEmpty {
                    result.append(ChatPDFSection(role: AppLocalized("Assistant"),
                                                 text: parts.joined(separator: "\n\n")))
                }

            case .systemInfo:
                let text = message.content.trimmingCharacters(in: .whitespacesAndNewlines)
                if !text.isEmpty {
                    result.append(ChatPDFSection(role: AppLocalized("System"), text: text))
                }

            case .compactDivider:
                result.append(ChatPDFSection(role: AppLocalized("System"),
                                             text: AppLocalized("Context compacted here.")))
            }
        }
        return result
    }

    private static func toolLine(_ block: AssistantBlock) -> String {
        let name: String
        switch block.kind {
        case .shellTool: name = "shell_execute"
        case .fileReadTool: name = "file_read"
        case .fileWriteTool: name = "file_write"
        case .fileEditTool: name = "file_edit"
        case .browserTool: name = "browser_use"
        case .readImageTool: name = "read_image"
        case .memoryTool(let action): name = action
        case .delegateTool: name = "delegate_task"
        case .text, .thinking, .info: name = "tool"
        }
        let flat = block.content
            .trimmingCharacters(in: .whitespacesAndNewlines)
            .replacingOccurrences(of: "\n", with: " ")
        let clipped = flat.count > 240 ? String(flat.prefix(240)) + "…" : flat
        return clipped.isEmpty ? "[\(name)]" : "[\(name)] \(clipped)"
    }

    // MARK: - Render

    /// Renders the sections into a paginated PDF. Safe to call off the main
    /// thread; only immutable value types cross the boundary.
    static func render(title: String, sections: [ChatPDFSection], date: Date = Date()) -> Data? {
        let attributed = makeAttributedString(title: title, sections: sections, date: date)
        guard attributed.length > 0 else { return nil }
        let framesetter = CTFramesetterCreateWithAttributedString(attributed)
        let contentRect = CGRect(
            x: margin,
            y: margin,
            width: pageSize.width - margin * 2,
            height: pageSize.height - margin * 2
        )
        let path = CGPath(rect: contentRect, transform: nil)

        let footerAttributes: [NSAttributedString.Key: Any] = [
            .font: UIFont.systemFont(ofSize: 8),
            .foregroundColor: UIColor.secondaryLabel,
        ]
        let dateFormatter = DateFormatter()
        dateFormatter.locale = Locale(identifier: "en_US_POSIX")
        dateFormatter.dateFormat = "yyyy-MM-dd HH:mm"
        let footerStamp = dateFormatter.string(from: date)

        let format = UIGraphicsPDFRendererFormat()
        format.documentInfo = [
            kCGPDFContextTitle as String: title,
            kCGPDFContextCreator as String: "Minis",
        ]
        let renderer = UIGraphicsPDFRenderer(bounds: CGRect(origin: .zero, size: pageSize), format: format)

        return renderer.pdfData { context in
            var location = 0
            var pageNumber = 1
            while location < attributed.length {
                context.beginPage()
                let cg = context.cgContext

                let frame = CTFramesetterCreateFrame(
                    framesetter,
                    CFRange(location: location, length: 0),
                    path,
                    nil
                )
                let visible = CTFrameGetVisibleStringRange(frame)
                guard visible.length > 0 else { break }

                cg.saveGState()
                cg.textMatrix = .identity
                cg.translateBy(x: 0, y: pageSize.height)
                cg.scaleBy(x: 1, y: -1)
                CTFrameDraw(frame, cg)
                cg.restoreGState()

                let footer = "Minis · \(footerStamp) · \(pageNumber)"
                footer.draw(
                    at: CGPoint(x: margin, y: pageSize.height - margin + 10),
                    withAttributes: footerAttributes
                )

                location += visible.length
                pageNumber += 1
            }
        }
    }

    private static func makeAttributedString(title: String,
                                             sections: [ChatPDFSection],
                                             date: Date) -> NSAttributedString {
        let result = NSMutableAttributedString()

        let titleStyle = NSMutableParagraphStyle()
        titleStyle.paragraphSpacing = 2
        result.append(NSAttributedString(string: title + "\n", attributes: [
            .font: UIFont.systemFont(ofSize: 19, weight: .bold),
            .foregroundColor: UIColor.label,
            .paragraphStyle: titleStyle,
        ]))

        let dateFormatter = DateFormatter()
        dateFormatter.dateStyle = .long
        dateFormatter.timeStyle = .short
        result.append(NSAttributedString(string: dateFormatter.string(from: date) + "\n\n", attributes: [
            .font: UIFont.systemFont(ofSize: 9),
            .foregroundColor: UIColor.secondaryLabel,
        ]))

        let roleStyle = NSMutableParagraphStyle()
        roleStyle.paragraphSpacingBefore = 10
        roleStyle.paragraphSpacing = 3

        let bodyStyle = NSMutableParagraphStyle()
        bodyStyle.lineSpacing = 2
        bodyStyle.paragraphSpacing = 8

        for section in sections {
            result.append(NSAttributedString(string: section.role.uppercased() + "\n", attributes: [
                .font: UIFont.systemFont(ofSize: 9, weight: .semibold),
                .foregroundColor: UIColor.systemBlue,
                .kern: 0.6,
                .paragraphStyle: roleStyle,
            ]))
            result.append(NSAttributedString(string: section.text + "\n", attributes: [
                .font: UIFont.systemFont(ofSize: 11),
                .foregroundColor: UIColor.label,
                .paragraphStyle: bodyStyle,
            ]))
        }
        return result
    }
}
