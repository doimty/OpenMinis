//
//  BalanceLookup.swift
//  MinisApp
//
//  Account balance is NOT a universal capability: every vendor exposes it
//  differently, and most API-key providers expose no account endpoint at all.
//  This file therefore maps an instance's base URL to ONE documented endpoint
//  and parses that vendor's own JSON. Anything else is reported as unsupported
//  rather than guessed — a fabricated number is worse than no number.
//
//  Foundation-only on purpose: the routing and parsing below run in a host
//  regression with fixtures, without the app target.
//

import Foundation

// MARK: - Model

enum BalanceSource: String, Equatable {
    case deepSeek
    case moonshot
    case siliconFlow
    case openRouter
}

enum BalanceAmountKind: String, Equatable, CaseIterable {
    case available
    case granted
    case toppedUp
    case cash
    case voucher
    case totalCredits
    case totalUsage
    /// Local arithmetic (credits − usage), never reported by the vendor.
    case remaining
    case total
}

struct BalanceAmount: Equatable {
    let kind: BalanceAmountKind
    let value: Double
    /// Provider-reported currency code, or "" when the endpoint doesn't say.
    let currency: String
    /// The exact token the provider returned (numbers can be strings).
    let raw: String
}

struct BalanceReport: Equatable {
    let source: BalanceSource
    let amounts: [BalanceAmount]
    /// True only for `remaining`, which this app computed itself.
    let remainingIsDerived: Bool
}

enum BalanceUnsupportedReason: String, Equatable {
    /// This vendor publishes no account-balance API for API keys.
    case noPublicEndpoint
    /// A custom/relay base URL — credentials are never sent to a host we
    /// cannot identify as the vendor's own.
    case customEndpoint
    /// OAuth-based providers (Copilot, Kimi Code, xAI…) have no balance API.
    case oauthCredential
    case insecureScheme
}

enum BalanceLookupError: Error, Equatable {
    case unauthorized
    case endpointMissing
    case httpStatus(Int)
    case malformedResponse
}

enum BalancePlan: Equatable {
    case request(url: URL, source: BalanceSource)
    case unsupported(BalanceUnsupportedReason)
}

// MARK: - Endpoint selection

enum BalanceLookup {
    /// The only hosts this build will attach a provider credential to, and the
    /// path each of them documents for an account-balance read.
    static func plan(baseURL: String?, credentialIsOAuth: Bool) -> BalancePlan {
        if credentialIsOAuth { return .unsupported(.oauthCredential) }
        guard let raw = baseURL?.trimmingCharacters(in: .whitespacesAndNewlines),
              !raw.isEmpty, let url = URL(string: raw),
              let host = url.host?.lowercased(), !host.isEmpty else {
            return .unsupported(.noPublicEndpoint)
        }
        guard url.scheme?.lowercased() == "https" else { return .unsupported(.insecureScheme) }

        let source: BalanceSource
        let path: String
        switch host {
        case "api.deepseek.com":
            source = .deepSeek
            path = "/user/balance"
        case "api.moonshot.cn", "api.moonshot.ai":
            source = .moonshot
            path = "/v1/users/me/balance"
        case "api.siliconflow.cn", "api.siliconflow.com":
            source = .siliconFlow
            path = "/v1/user/info"
        case "openrouter.ai":
            source = .openRouter
            path = "/api/v1/credits"
        case "api.openai.com", "api.anthropic.com", "generativelanguage.googleapis.com",
             "api.x.ai", "api.kimi.com", "api.kimi.ai":
            // Official hosts of providers that publish no API-key balance
            // endpoint. Distinguished from a relay so the UI can say WHY.
            return .unsupported(.noPublicEndpoint)
        default:
            // Relays and unknown hosts: never attach a credential to a host we
            // cannot identify as the vendor's own.
            return .unsupported(.customEndpoint)
        }

        // Rebuild from the ORIGIN: a configured base may carry /v1 (DeepSeek) or
        // /api (OpenRouter), and appending a documented absolute path to it
        // would produce /v1/user/balance or /api/v1/users/me/balance.
        var components = URLComponents()
        components.scheme = "https"
        components.host = url.host
        components.port = url.port
        components.path = path
        guard let built = components.url else { return .unsupported(.noPublicEndpoint) }
        return .request(url: built, source: source)
    }

    // MARK: - Parsing

    static func parse(source: BalanceSource, data: Data, statusCode: Int) -> Result<BalanceReport, BalanceLookupError> {
        switch statusCode {
        case 200...299:
            break
        case 401, 403:
            return .failure(.unauthorized)
        case 404, 405:
            return .failure(.endpointMissing)
        default:
            return .failure(.httpStatus(statusCode))
        }
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let root = object as? [String: Any] else {
            return .failure(.malformedResponse)
        }
        switch source {
        case .deepSeek: return parseDeepSeek(root)
        case .moonshot: return parseMoonshot(root)
        case .siliconFlow: return parseSiliconFlow(root)
        case .openRouter: return parseOpenRouter(root)
        }
    }

    private static func parseDeepSeek(_ root: [String: Any]) -> Result<BalanceReport, BalanceLookupError> {
        guard let infos = root["balance_infos"] as? [[String: Any]], !infos.isEmpty else {
            return .failure(.malformedResponse)
        }
        var amounts: [BalanceAmount] = []
        for info in infos {
            let currency = text(info["currency"]) ?? ""
            append(&amounts, .available, info["total_balance"], currency)
            append(&amounts, .granted, info["granted_balance"], currency)
            append(&amounts, .toppedUp, info["topped_up_balance"], currency)
        }
        guard !amounts.isEmpty else { return .failure(.malformedResponse) }
        return .success(BalanceReport(source: .deepSeek, amounts: amounts, remainingIsDerived: false))
    }

    private static func parseMoonshot(_ root: [String: Any]) -> Result<BalanceReport, BalanceLookupError> {
        guard let data = root["data"] as? [String: Any] else { return .failure(.malformedResponse) }
        var amounts: [BalanceAmount] = []
        // Moonshot's endpoint reports no currency code; do not invent one.
        append(&amounts, .available, data["available_balance"], "")
        append(&amounts, .voucher, data["voucher_balance"], "")
        append(&amounts, .cash, data["cash_balance"], "")
        guard !amounts.isEmpty else { return .failure(.malformedResponse) }
        return .success(BalanceReport(source: .moonshot, amounts: amounts, remainingIsDerived: false))
    }

    private static func parseSiliconFlow(_ root: [String: Any]) -> Result<BalanceReport, BalanceLookupError> {
        if let status = root["status"] as? Bool, status == false { return .failure(.malformedResponse) }
        guard let data = root["data"] as? [String: Any] else { return .failure(.malformedResponse) }
        var amounts: [BalanceAmount] = []
        append(&amounts, .available, data["balance"], "")
        append(&amounts, .total, data["totalBalance"], "")
        append(&amounts, .toppedUp, data["chargeBalance"], "")
        guard !amounts.isEmpty else { return .failure(.malformedResponse) }
        return .success(BalanceReport(source: .siliconFlow, amounts: amounts, remainingIsDerived: false))
    }

    private static func parseOpenRouter(_ root: [String: Any]) -> Result<BalanceReport, BalanceLookupError> {
        guard let data = root["data"] as? [String: Any] else { return .failure(.malformedResponse) }
        let credits = number(data["total_credits"])
        let usage = number(data["total_usage"])
        var amounts: [BalanceAmount] = []
        if let credits {
            amounts.append(BalanceAmount(kind: .totalCredits, value: credits, currency: "USD",
                                         raw: text(data["total_credits"]) ?? String(credits)))
        }
        if let usage {
            amounts.append(BalanceAmount(kind: .totalUsage, value: usage, currency: "USD",
                                         raw: text(data["total_usage"]) ?? String(usage)))
        }
        guard !amounts.isEmpty else { return .failure(.malformedResponse) }
        var derived = false
        if let credits, let usage {
            // Credits bought and credits spent are different quantities; the
            // difference is this app's arithmetic, and the UI labels it so.
            amounts.append(BalanceAmount(kind: .remaining, value: credits - usage, currency: "USD",
                                         raw: String(credits - usage)))
            derived = true
        }
        return .success(BalanceReport(source: .openRouter, amounts: amounts, remainingIsDerived: derived))
    }

    // MARK: - Tolerant field access

    private static func append(_ into: inout [BalanceAmount], _ kind: BalanceAmountKind,
                               _ any: Any?, _ currency: String) {
        guard let value = number(any) else { return }
        into.append(BalanceAmount(kind: kind, value: value, currency: currency,
                                  raw: text(any) ?? String(value)))
    }

    /// Vendors mix JSON numbers and numeric strings (`"110.00"`). Accept both;
    /// reject booleans, which bridge to NSNumber and would read as 1/0.
    static func number(_ any: Any?) -> Double? {
        if let value = any as? NSNumber {
            if CFGetTypeID(value) == CFBooleanGetTypeID() { return nil }
            return value.doubleValue
        }
        if let string = any as? String {
            return Double(string.trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return nil
    }

    static func text(_ any: Any?) -> String? {
        if let string = any as? String { return string }
        if let value = any as? NSNumber {
            if CFGetTypeID(value) == CFBooleanGetTypeID() { return nil }
            return value.stringValue
        }
        return nil
    }
}
