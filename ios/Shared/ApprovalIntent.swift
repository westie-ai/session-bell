import AppIntents
import Foundation

/// Backs the 允许/拒绝 buttons on the Live Activity card. LiveActivityIntent
/// runs in the app's process in the background — network access included.
@available(iOS 17.0, *)
struct ApprovalIntent: LiveActivityIntent {
    static var title: LocalizedStringResource = "批准授权"
    static var isDiscoverable: Bool = false

    @Parameter(title: "Backend")
    var backend: String

    @Parameter(title: "Secret")
    var secret: String

    @Parameter(title: "RequestID")
    var requestId: String

    @Parameter(title: "Decision")
    var decision: String

    init() {}

    init(backend: String, secret: String, requestId: String, decision: String) {
        self.backend = backend
        self.secret = secret
        self.requestId = requestId
        self.decision = decision
    }

    func perform() async throws -> some IntentResult {
        await SBBackend.post(
            "/api/decision",
            body: ["request_id": requestId, "decision": decision],
            to: backend, secret: secret)
        return .result()
    }
}
