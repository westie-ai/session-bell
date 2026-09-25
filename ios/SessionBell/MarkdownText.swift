import SwiftUI
import UIKit

/// Lightweight block-level markdown renderer: headers, lists, fenced code,
/// pipe tables, rules, and inline styles (bold/italic/code/links) via
/// AttributedString. Your prompts (`### ❯ …` + `> ` continuation lines from
/// the Mac) render as a tinted card so they stand apart from the agent's reply.
/// Long-press a block to copy it, or the whole section it sits in — a run of
/// reply text bounded by prompts, tool-call lines and `---` rules (e.g. a
/// drafted announcement fenced with rules), copied as plain text.
struct MarkdownText: View {
    let text: String

    private enum Block {
        case heading(String, Int)
        case bullet([String])
        case code(String)
        case paragraph(String)
        case table(header: [String]?, rows: [[String]])
        case rule
        /// Your prompt: `### ❯ first line` plus `> ` continuation lines.
        case prompt(String)

        /// Prompts, tool-call lists and rules split sections.
        var isSeparator: Bool {
            switch self {
            case .rule, .prompt: return true
            case .bullet(let items): return items.allSatisfy { $0.hasPrefix("🔧") }
            default: return false
            }
        }
    }

    var body: some View {
        let blocks = parse()
        let sections = sectionIndex(blocks)
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { i, block in
                view(for: block)
                    .contextMenu {
                        if case .rule = block {} else {
                            Button {
                                UIPasteboard.general.string = plain(block)
                            } label: { Label("Copy", systemImage: "doc.on.doc") }
                        }
                        if let s = sections[i],
                           sections.filter({ $0 == s }).count > 1 {
                            Button {
                                UIPasteboard.general.string = blocks.indices
                                    .filter { sections[$0] == s }
                                    .map { plain(blocks[$0]) }
                                    .joined(separator: "\n\n")
                            } label: { Label("Copy Section", systemImage: "doc.on.clipboard") }
                        }
                    }
            }
        }
    }

    @ViewBuilder
    private func view(for block: Block) -> some View {
        switch block {
        case .prompt(let text):
            // Chat-style bubble on the right, tail corner bottom-right; the
            // agent's reply stays full-width on the left.
            HStack {
                Spacer(minLength: 44)
                Text(inline(text))
                    .font(.subheadline)
                    .foregroundStyle(Color.sbInk)
                    .padding(.vertical, 9)
                    .padding(.horizontal, 13)
                    .background(Color.sbApprovalSoft,
                                in: UnevenRoundedRectangle(topLeadingRadius: 18, bottomLeadingRadius: 18,
                                                           bottomTrailingRadius: 5, topTrailingRadius: 18))
            }
            .padding(.top, 6)
        case .heading(let text, let level):
            Text(inline(text))
                .font(level == 1 ? .title3.bold()
                      : level == 2 ? .headline : .subheadline.bold())
        case .bullet(let items):
            VStack(alignment: .leading, spacing: 3) {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
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
        case .rule:
            Divider().padding(.vertical, 4)
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

    /// Section number per block; separators belong to none.
    private func sectionIndex(_ blocks: [Block]) -> [Int?] {
        var n = 0
        var open = false
        return blocks.map { b in
            if b.isSeparator {
                if open { n += 1; open = false }
                return nil
            }
            open = true
            return n
        }
    }

    /// What lands on the pasteboard: inline markup stripped, list dashes kept.
    private func plain(_ block: Block) -> String {
        func strip(_ s: String) -> String { String(inline(s).characters) }
        switch block {
        case .heading(let s, _): return strip(s)
        case .bullet(let items): return items.map { "- " + strip($0) }.joined(separator: "\n")
        case .code(let s): return s
        case .paragraph(let s): return strip(s)
        case .prompt(let s): return strip(s)
        case .table(let header, let rows):
            return ((header.map { [$0] } ?? []) + rows)
                .map { $0.map(strip).joined(separator: "\t") }
                .joined(separator: "\n")
        case .rule: return ""
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
        var prompt: [String]? = nil
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
        func flushPrompt() {
            if let lines = prompt { blocks.append(.prompt(lines.joined(separator: "\n"))) }
            prompt = nil
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
            if prompt != nil, line.hasPrefix(">") {
                prompt?.append(String(line.dropFirst(line.hasPrefix("> ") ? 2 : 1)))
                continue
            }
            flushPrompt()
            if line.hasPrefix("```") {
                if let lines = codeLines {
                    blocks.append(.code(lines.joined(separator: "\n")))
                    codeLines = nil
                } else {
                    flushTable(); flushBullets(); flushParagraph()
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
            if line.hasPrefix("### ❯") {
                flushBullets(); flushParagraph()
                prompt = [String(line.dropFirst(5)).trimmingCharacters(in: .whitespaces)]
            } else if line.range(of: #"^(-{3,}|\*{3,}|_{3,})$"#, options: .regularExpression) != nil {
                flushBullets(); flushParagraph()
                blocks.append(.rule)
            } else if let match = line.range(of: #"^#{1,3}\s+"#, options: .regularExpression) {
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
        flushPrompt(); flushTable(); flushBullets(); flushParagraph()
        return blocks
    }
}
