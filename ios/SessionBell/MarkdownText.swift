import SwiftUI

/// Lightweight block-level markdown renderer: headers, lists, fenced code,
/// and inline styles (bold/italic/code/links) via AttributedString.
struct MarkdownText: View {
    let text: String

    private enum Block: Identifiable {
        case heading(String, Int)
        case bullet([String])
        case code(String)
        case paragraph(String)

        var id: String {
            switch self {
            case .heading(let s, let l): return "h\(l)-\(s)"
            case .bullet(let items): return "b-" + items.joined(separator: "|")
            case .code(let s): return "c-\(s.hashValue)"
            case .paragraph(let s): return "p-\(s.hashValue)"
            }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(parse()) { block in
                switch block {
                case .heading(let text, let level):
                    Text(inline(text))
                        .font(level == 1 ? .title3.bold()
                              : level == 2 ? .headline : .subheadline.bold())
                case .bullet(let items):
                    VStack(alignment: .leading, spacing: 3) {
                        ForEach(items, id: \.self) { item in
                            HStack(alignment: .top, spacing: 6) {
                                Text("•").foregroundStyle(.secondary)
                                Text(inline(item)).font(.subheadline)
                            }
                        }
                    }
                case .code(let code):
                    ScrollView(.horizontal, showsIndicators: false) {
                        Text(code)
                            .font(.system(.caption, design: .monospaced))
                            .padding(8)
                    }
                    .background(Color(.secondarySystemBackground),
                                in: RoundedRectangle(cornerRadius: 8))
                case .paragraph(let text):
                    Text(inline(text)).font(.subheadline)
                }
            }
        }
    }

    private func inline(_ s: String) -> AttributedString {
        (try? AttributedString(
            markdown: s,
            options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(s)
    }

    private func parse() -> [Block] {
        var blocks: [Block] = []
        var codeLines: [String]? = nil
        var bullets: [String] = []
        var paragraph: [String] = []

        func flushBullets() {
            if !bullets.isEmpty { blocks.append(.bullet(bullets)); bullets = [] }
        }
        func flushParagraph() {
            let joined = paragraph.joined(separator: "\n")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !joined.isEmpty { blocks.append(.paragraph(joined)) }
            paragraph = []
        }

        for rawLine in text.components(separatedBy: "\n") {
            let line = rawLine.trimmingCharacters(in: .whitespaces)
            if line.hasPrefix("```") {
                if let lines = codeLines {
                    blocks.append(.code(lines.joined(separator: "\n")))
                    codeLines = nil
                } else {
                    flushBullets(); flushParagraph()
                    codeLines = []
                }
                continue
            }
            if codeLines != nil {
                codeLines?.append(rawLine)
                continue
            }
            if let match = line.range(of: #"^#{1,3}\s+"#, options: .regularExpression) {
                flushBullets(); flushParagraph()
                let level = line.prefix(while: { $0 == "#" }).count
                blocks.append(.heading(String(line[match.upperBound...]), level))
            } else if let match = line.range(of: #"^([-*]|\d+\.)\s+"#, options: .regularExpression) {
                flushParagraph()
                bullets.append(String(line[match.upperBound...]))
            } else if line.isEmpty {
                flushBullets(); flushParagraph()
            } else {
                flushBullets()
                paragraph.append(line)
            }
        }
        if let lines = codeLines { blocks.append(.code(lines.joined(separator: "\n"))) }
        flushBullets(); flushParagraph()
        return blocks
    }
}
