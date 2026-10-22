//
//  BalanceService.swift
//  MinisApp
//
//  On-demand account balance for the providers that actually publish one.
//  Deliberately narrow: one GET per provider instance, official hosts only,
//  no background polling, no persisted cache beyond the in-memory window.
//  The API key never leaves the request header and is never logged.
//

import Foundation
import Combine

@MainActor
final class BalanceService: ObservableObject {
    enum State: Equatable {
        case idle
        case loading
        case ready(BalanceReport, Date)
        case missingCredential
        case unsupported(BalanceUnsupportedReason)
        case failed(String)
    }

    static let shared = BalanceService()

    /// Short in-memory cache so reopening the detail screen doesn't re-hit the
    /// vendor. Not persisted: a restart re-reads, and an expired balance is
    /// never shown as current.
    static let cacheLifetime: TimeInterval = 300

    @Published private(set) var states: [String: State] = [:]
    private var inFlight: Set<String> = []
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func state(for instanceId: String) -> State {
        states[instanceId] ?? .idle
    }

    func clear(instanceId: String) {
        states[instanceId] = nil
        inFlight.remove(instanceId)
    }

    /// `force` is the user's Refresh button; the automatic call on appear is
    /// not forced and honours the cache window.
    func refresh(_ instance: ProviderInstance, force: Bool = false) {
        let id = instance.id
        guard !inFlight.contains(id) else { return }
        if !force, case .ready(_, let at) = state(for: id),
           Date().timeIntervalSince(at) < Self.cacheLifetime {
            return
        }
        guard instance.credentialType == .apiKey else {
            states[id] = .unsupported(.oauthCredential)
            return
        }
        guard let key = ProviderKeychainHelper.loadAPIKey(instanceId: id, caller: "BalanceService"),
              !key.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            states[id] = .missingCredential
            return
        }
        let plan = BalanceLookup.plan(baseURL: Self.baseURL(for: instance), credentialIsOAuth: false)
        guard case .request(let url, let source) = plan else {
            if case .unsupported(let reason) = plan { states[id] = .unsupported(reason) }
            return
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.timeoutInterval = 15
        request.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        inFlight.insert(id)
        states[id] = .loading
        session.dataTask(with: request) { [weak self] data, response, error in
            Task { @MainActor in
                guard let self else { return }
                self.inFlight.remove(id)
                if let error {
                    self.states[id] = .failed(error.localizedDescription)
                    return
                }
                let status = (response as? HTTPURLResponse)?.statusCode ?? 0
                guard let data else {
                    self.states[id] = .failed(AppLocalized("The provider returned an empty response."))
                    return
                }
                switch BalanceLookup.parse(source: source, data: data, statusCode: status) {
                case .success(let report):
                    self.states[id] = .ready(report, Date())
                case .failure(let error):
                    self.states[id] = .failed(Self.message(for: error))
                }
            }
        }.resume()
    }

    static func message(for error: BalanceLookupError) -> String {
        switch error {
        case .unauthorized:
            return AppLocalized("The API key was rejected.")
        case .endpointMissing:
            return AppLocalized("This endpoint is not available for this account.")
        case .httpStatus(let code):
            return String(format: AppLocalized("The provider returned HTTP %lld."), code)
        case .malformedResponse:
            return AppLocalized("The response format was not recognized.")
        }
    }

    /// The vendor's own base URL when the instance has no custom one. Kept in
    /// step with `ProviderConfigStore.modelsDevBaseURL`, which is private.
    static func baseURL(for instance: ProviderInstance) -> String? {
        if let custom = instance.effectiveCustomBaseURL { return custom }
        switch instance.providerType {
        case .openRouter: return "https://openrouter.ai/api"
        case .openAI, .openAIResponses: return "https://api.openai.com"
        case .anthropic: return "https://api.anthropic.com"
        case .gemini: return "https://generativelanguage.googleapis.com"
        case .xAI: return "https://api.x.ai"
        case .kimiCode: return "https://api.kimi.com/coding"
        case .githubCopilot, .antigravity, .unsupported: return nil
        }
    }
}
