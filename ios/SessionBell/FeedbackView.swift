import SwiftUI
import UIKit

/// 设置页 → 「发送反馈」。正文 + 可选联系方式,连同 App / 系统版本一起
/// POST 到 /api/feedback;后端落库并转发到邮箱。失败时给一个 mailto 兜底。
struct FeedbackView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var kind: Kind = .feedback
    @State private var text = ""
    @State private var contact = ""
    @State private var sending = false
    @State private var sent = false
    @State private var failed = false
    @FocusState private var textFocused: Bool

    enum Kind: String, CaseIterable, Identifiable {
        case feedback, bug, idea
        var id: String { rawValue }
        var label: LocalizedStringKey {
            switch self {
            case .feedback: return "Feedback"
            case .bug: return "Bug"
            case .idea: return "Idea"
            }
        }
    }

    static let mailbox = "begin1314@gmail.com"

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Type", selection: $kind) {
                        ForEach(Kind.allCases) { k in Text(k.label).tag(k) }
                    }
                    .pickerStyle(.segmented)
                    TextEditor(text: $text)
                        .frame(minHeight: 140)
                        .focused($textFocused)
                        .overlay(alignment: .topLeading) {
                            if text.isEmpty {
                                Text("What happened, or what would make SessionBell better?")
                                    .foregroundStyle(.tertiary)
                                    .padding(.top, 8)
                                    .padding(.leading, 4)
                                    .allowsHitTesting(false)
                            }
                        }
                } footer: {
                    Text("Sent with your app version and iOS version so we can reproduce it. No session content is included.")
                }
                Section {
                    TextField("Email or any handle — X, Discord… (optional)", text: $contact)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .keyboardType(.emailAddress)
                } header: {
                    Text("How can we reach you?")
                }
                if failed {
                    Section {
                        Label("Couldn't send right now. Try again, or email us directly.", systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.orange)
                        if let url = mailtoURL {
                            Link(destination: url) {
                                Label("Email \(Self.mailbox)", systemImage: "envelope")
                            }
                        }
                    }
                }
            }
            .navigationTitle("Send Feedback")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if sending {
                        ProgressView()
                    } else {
                        Button("Send") { submit() }
                            .disabled(text.trimmingCharacters(in: .whitespacesAndNewlines).count < 2)
                    }
                }
            }
            .onAppear { textFocused = true }
            .alert("Thanks! Feedback sent.", isPresented: $sent) {
                Button("OK") { dismiss() }
            }
        }
    }

    private var payload: [String: String] {
        let info = Bundle.main.infoDictionary
        return [
            "kind": kind.rawValue,
            "text": text.trimmingCharacters(in: .whitespacesAndNewlines),
            "contact": contact.trimmingCharacters(in: .whitespaces),
            "app_version": info?["CFBundleShortVersionString"] as? String ?? "?",
            "build": info?["CFBundleVersion"] as? String ?? "?",
            "os": "\(UIDevice.current.systemName) \(UIDevice.current.systemVersion)",
            "device": Self.modelIdentifier,
            "locale": Locale.current.identifier,
        ]
    }

    private var mailtoURL: URL? {
        let p = payload
        let body = "\(p["text"] ?? "")\n\n——\nApp \(p["app_version"] ?? "") (\(p["build"] ?? ""))  \(p["os"] ?? "")  \(p["device"] ?? "")"
        var c = URLComponents()
        c.scheme = "mailto"
        c.path = Self.mailbox
        c.queryItems = [
            URLQueryItem(name: "subject", value: "[SessionBell] \(kind.rawValue)"),
            URLQueryItem(name: "body", value: body),
        ]
        return c.url
    }

    private func submit() {
        guard !sending else { return }
        sending = true
        failed = false
        Task {
            let ok = await SBBackend.postChecked("/api/feedback", body: payload)
            sending = false
            if ok { sent = true } else { failed = true }
        }
    }

    static var modelIdentifier: String {
        var sys = utsname()
        uname(&sys)
        return withUnsafeBytes(of: &sys.machine) { raw in
            String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self)
        }
    }
}
