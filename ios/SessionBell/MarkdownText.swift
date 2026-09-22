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
        case table(header: [String]?, rows: [[String]])

        var id: String {
            switch self {
            case .heading(let s, let l): return "h\(l)-\(s)"
            case .bullet(let items): return "b-" + items.joined(separator: "|")
            case .code(let s): return "c-\(s.hashValue)"
            case .paragraph(let s): return "p-\(s.hashValue)"
            case .table(let h, let r): return "t-\((h ?? []).hashValue)-\(r.hashValue)"
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
                case .table(let header, let rows):
                    table(header: header, rows: rows)
                }
            }
        }
    }

    /// Pipe tables: header row tinted, hairline between rows, cells wrap at a
    /// sane width, whole thing scrolls sideways when it is wider than the phone.
    @ViewBuilder
    private func table(header: [String]?, rows: [[String]]) -> some View {
        let all = (header.map { [$0] } ?? []) + rows
        let columns = all.map(\.count).max() ?? 0
        ScrollView(.horizontal, showsIndicators: false) {
            Grid(alignment: .topLeading, horizontalSpacing: 0, verticalSpacing: 0) {
                ForEach(Array(all.enumerated()), id: \.offset) { index, row in
                    let isHeader = header != nil && index == 0
                    GridRow {
                        ForEach(0..<columns, id: \.self) { c in
                            Text(inline(c < row.count ? row[c] : ""))
                                .font(.caption)
                                .fontWeight(isHeader ? .semibold : .regular)
                                .foregroundStyle(isHeader ? Color.sbInk2 : Color.sbInk)
                                .frame(maxWidth: 220, alignment: .leading)
                                .fixedSize(horizontal: false, vertical: true)
                                .padding(.vertical, 7)
                                .padding(.horizontal, 10)
                        }
                    }
                    .background(isHeader ? Color(.tertiarySystemFill) : Color.clear)
                    if index < all.count - 1 {
                        Divider().gridCellUnsizedAxes(.horizontal)
                    }
                }
            }
        }
        .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 8))
        .clipShape(RoundedRectangle(cornerRadius: 8))
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
        var tableRows: [[String]] = []
        var tableHeader: [String]? = nil
        var tableSawSeparator = false

        func cells(_ line: String) -> [String] {
            var body = Substring(line)
            if body.hasPrefix("|") { body = body.dropFirst() }
            if body.hasSuffix("|") { body = body.dropLast() }
            return body.components(separatedBy: "|").map { $0.trimmingCharacters(in: .whitespaces) }
        }
        func isSeparator(_ cells: [String]) -> Bool {
            !cells.isEmpty && cells.allSatisfy {
                $0.range(of: #"^:?-{2,}:?$"#, options: .regularExpression) != nil
            }
        }
        func flushTable() {
            if !tableRows.isEmpty || tableHeader != nil {
                blocks.append(.table(header: tableHeader, rows: tableRows))
            }
            tableRows = []; tableHeader = nil; tableSawSeparator = false
        }

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
            if line.hasPrefix("|") && line.count > 1 {
                flushBullets(); flushParagraph()
                let row = cells(line)
                if isSeparator(row) {
                    if !tableSawSeparator, tableHeader == nil, let first = tableRows.first, tableRows.count == 1 {
                        tableHeader = first; tableRows = []
                    }
                    tableSawSeparator = true
                } else {
                    tableRows.append(row)
                }
                continue
            }
            flushTable()
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
        flushTable(); flushBullets(); flushParagraph()
        return blocks
    }
}
