import Foundation

// Runs against the production Foundation helper on the pinned macOS runner.
// The old Swift regex is an oracle here only, never an iOS-15 production path.
@main
struct MarkdownImagePatternTests {
    static func main() {
        let samples = [
            "",
            "plain text and [link](https://example.com)",
            "![alt](image.png)",
            "![](image.png)",
            "before ![one](one.png) between ![two](two.png) after",
            "中文😀e\u{301}前缀 ![封面🧑‍💻](minis://shared/图像.png) 后缀",
            "![line\nalt](a\nb.png)",
            "![partial](unfinished",
            "![empty]()",
            "\\![escaped](a.png)",
            "![nested](a(b).png)",
            "![title](a.png \"title\")",
            "![wide](minis://shared/图｜像.png)\n![next](https://example.com/x)",
        ]
        let originalPattern = try! Regex(#"!\[([^\]]*)\]\(([^)]+)\)"#)
        for (index, sample) in samples.enumerated() {
            let expected = sample.ranges(of: originalPattern).map { String(sample[$0]) }
            let actual = MarkdownStripper.imageSyntaxMatches(in: sample)
            precondition(actual == expected, "image diagnostic match mismatch at fixture \(index)")
        }
        precondition(MarkdownStripper.imageSyntaxMatches(in: "中文😀 ![图](x.png)") == ["![图](x.png)"])
        print("PASS: production image diagnostics match the old regex for \(samples.count) fixtures")
    }
}
