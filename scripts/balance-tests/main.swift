import Foundation

// Host regression for BalanceLookup: endpoint routing, safety refusals and
// vendor JSON parsing. No network, no credentials, no app target.

var failures = 0
var checks = 0
func expect(_ condition: Bool, _ message: String) {
    checks += 1
    if !condition {
        failures += 1
        FileHandle.standardError.write(("FAIL: " + message + "\n").data(using: .utf8)!)
    }
}

func requestURL(_ base: String?, oauth: Bool = false) -> String? {
    if case .request(let url, _) = BalanceLookup.plan(baseURL: base, credentialIsOAuth: oauth) {
        return url.absoluteString
    }
    return nil
}
func refusal(_ base: String?, oauth: Bool = false) -> BalanceUnsupportedReason? {
    if case .unsupported(let reason) = BalanceLookup.plan(baseURL: base, credentialIsOAuth: oauth) {
        return reason
    }
    return nil
}
func report(_ source: BalanceSource, _ json: String, _ status: Int = 200) -> BalanceReport? {
    if case .success(let r) = BalanceLookup.parse(source: source, data: Data(json.utf8), statusCode: status) {
        return r
    }
    return nil
}
func lookupError(_ source: BalanceSource, _ json: String, _ status: Int) -> BalanceLookupError? {
    if case .failure(let e) = BalanceLookup.parse(source: source, data: Data(json.utf8), statusCode: status) {
        return e
    }
    return nil
}

// MARK: - 1. Endpoint routing

do {
    expect(requestURL("https://api.deepseek.com") == "https://api.deepseek.com/user/balance",
           "DeepSeek official base maps to /user/balance")
    // The documented path is absolute. A base that already carries /v1 must not
    // produce /v1/user/balance.
    expect(requestURL("https://api.deepseek.com/v1") == "https://api.deepseek.com/user/balance",
           "DeepSeek /v1 base does not duplicate the version segment")
    expect(requestURL("https://api.deepseek.com/v1/") == "https://api.deepseek.com/user/balance",
           "DeepSeek trailing slash is normalized")
    expect(requestURL("https://api.deepseek.com/") == "https://api.deepseek.com/user/balance",
           "DeepSeek bare slash is normalized")

    expect(requestURL("https://api.moonshot.cn/v1") == "https://api.moonshot.cn/v1/users/me/balance",
           "Moonshot v1 base maps to the balance endpoint")
    expect(requestURL("https://api.moonshot.ai/v1") == "https://api.moonshot.ai/v1/users/me/balance",
           "Moonshot international host is supported")

    expect(requestURL("https://api.siliconflow.cn/v1") == "https://api.siliconflow.cn/v1/user/info",
           "SiliconFlow maps to user info")
    expect(requestURL("https://api.siliconflow.com/v1") == "https://api.siliconflow.com/v1/user/info",
           "SiliconFlow .com host is supported")

    expect(requestURL("https://openrouter.ai/api") == "https://openrouter.ai/api/v1/credits",
           "OpenRouter /api base maps to credits")
    expect(requestURL("https://openrouter.ai/api/v1") == "https://openrouter.ai/api/v1/credits",
           "OpenRouter with /v1 does not duplicate the segment")

    expect(requestURL("https://api.deepseek.com:8443/v1") == "https://api.deepseek.com:8443/user/balance",
           "an explicit port is preserved")
}

// MARK: - 2. Safety refusals

do {
    expect(refusal(nil) == .noPublicEndpoint, "no base URL means no lookup")
    expect(refusal("   ") == .noPublicEndpoint, "blank base URL means no lookup")
    expect(refusal("http://api.deepseek.com") == .insecureScheme, "plain HTTP is refused")
    expect(refusal("https://api.deepseek.com", oauth: true) == .oauthCredential,
           "OAuth instances are refused even on a supported host")
    expect(refusal("https://api.openai.com/v1") == .noPublicEndpoint,
           "OpenAI official host reports 'no public endpoint', not 'custom'")
    expect(refusal("https://api.anthropic.com") == .noPublicEndpoint,
           "Anthropic official host reports 'no public endpoint'")
    expect(refusal("https://generativelanguage.googleapis.com") == .noPublicEndpoint,
           "Gemini official host reports 'no public endpoint'")
    expect(refusal("https://api.x.ai/v1") == .noPublicEndpoint,
           "xAI official host reports 'no public endpoint'")
    expect(refusal("https://my-relay.example.com/v1") == .customEndpoint,
           "an unknown relay is refused")
    // Host-suffix confusion must not pass: api.deepseek.com.evil.tld is NOT the vendor.
    expect(refusal("https://api.deepseek.com.evil.tld/v1") == .customEndpoint,
           "a host that merely ends with the vendor name is refused")
    expect(refusal("https://user@api.deepseek.com/") == .customEndpoint || requestURL("https://user@api.deepseek.com/") != nil,
           "userinfo in the URL does not crash the router")
}

// MARK: - 3. Every routing result stays on the input host

do {
    let bases = [
        "https://api.deepseek.com/v1", "https://api.moonshot.cn/v1",
        "https://api.siliconflow.cn/v1", "https://openrouter.ai/api",
        "https://api.openai.com/v1", "https://relay.example.com/anything",
    ]
    for base in bases {
        guard let built = requestURL(base) else { continue }
        expect(URL(string: built)?.host == URL(string: base)?.host,
               "routing for \(base) never changes host")
        expect(built.hasPrefix("https://"), "routing for \(base) stays on HTTPS")
    }
}

// MARK: - 4. DeepSeek payload

do {
    let json = #"""
    {"is_available":true,"balance_infos":[{"currency":"CNY","total_balance":"110.00","granted_balance":"10.00","topped_up_balance":"100.00"}]}
    """#
    guard let r = report(.deepSeek, json) else { fatalError("DeepSeek payload did not parse") }
    expect(r.amounts.count == 3, "DeepSeek yields available/granted/topped-up")
    expect(r.amounts.first?.value == 110 && r.amounts.first?.currency == "CNY",
           "DeepSeek numeric strings are decoded with currency")
    expect(r.amounts.contains { $0.kind == .toppedUp && $0.value == 100 }, "topped_up_balance is kept separate")
    expect(!r.remainingIsDerived, "DeepSeek figures are all provider-reported")

    let multi = #"""
    {"balance_infos":[{"currency":"CNY","total_balance":"1.00"},{"currency":"USD","total_balance":"2.00"}]}
    """#
    expect(report(.deepSeek, multi)?.amounts.count == 2, "multiple currency entries are both kept")

    expect(report(.deepSeek, #"{"is_available":true}"#) == nil, "missing balance_infos is malformed")
    expect(report(.deepSeek, #"{"balance_infos":[]}"#) == nil, "an empty balance_infos is malformed")
    expect(report(.deepSeek, #"{"balance_infos":[{"currency":"CNY","total_balance":true}]}"#) == nil,
           "a boolean is never accepted as an amount")
}

// MARK: - 5. Moonshot / SiliconFlow payloads

do {
    let moonshot = #"""
    {"code":0,"data":{"available_balance":49.58,"voucher_balance":5,"cash_balance":44.58},"status":true}
    """#
    guard let m = report(.moonshot, moonshot) else { fatalError("Moonshot payload did not parse") }
    expect(m.amounts.count == 3, "Moonshot yields available/voucher/cash")
    expect(m.amounts.allSatisfy { $0.currency.isEmpty },
           "Moonshot reports no currency, so none is invented")

    let silicon = #"""
    {"code":20000,"message":"OK","status":true,"data":{"balance":"98.00","chargeBalance":"100.00","totalBalance":"98.00"}}
    """#
    guard let s = report(.siliconFlow, silicon) else { fatalError("SiliconFlow payload did not parse") }
    expect(s.amounts.count == 3, "SiliconFlow yields balance/total/charge")
    expect(s.amounts.first?.value == 98, "SiliconFlow numeric strings decode")

    let failed = #"""
    {"code":40001,"status":false,"data":{}}
    """#
    expect(report(.siliconFlow, failed) == nil, "SiliconFlow status=false is not a balance")
}

// MARK: - 6. OpenRouter credits

do {
    let both = #"""
    {"data":{"total_credits":10,"total_usage":2.5}}
    """#
    guard let r = report(.openRouter, both) else { fatalError("OpenRouter payload did not parse") }
    expect(r.amounts.count == 3, "credits, usage and the derived remainder are reported separately")
    expect(r.amounts.contains { $0.kind == .totalCredits && $0.value == 10 }, "credits purchased is exact")
    expect(r.amounts.contains { $0.kind == .totalUsage && $0.value == 2.5 }, "usage is exact")
    expect(r.amounts.contains { $0.kind == .remaining && $0.value == 7.5 }, "remaining is credits minus usage")
    expect(r.remainingIsDerived, "the remainder is flagged as locally calculated")

    let creditsOnly = #"""
    {"data":{"total_credits":10}}
    """#
    guard let c = report(.openRouter, creditsOnly) else { fatalError("credits-only payload did not parse") }
    expect(c.amounts.count == 1 && !c.amounts.contains { $0.kind == .remaining },
           "without usage there is no fabricated remainder")
    expect(!c.remainingIsDerived, "no remainder means nothing is marked derived")
}

// MARK: - 7. HTTP and shape errors

do {
    expect(lookupError(.deepSeek, "{}", 401) == .unauthorized, "401 maps to unauthorized")
    expect(lookupError(.deepSeek, "{}", 403) == .unauthorized, "403 maps to unauthorized")
    expect(lookupError(.deepSeek, "{}", 404) == .endpointMissing, "404 maps to endpoint missing")
    expect(lookupError(.deepSeek, "{}", 429) == .httpStatus(429), "429 keeps its status")
    expect(lookupError(.deepSeek, "{}", 500) == .httpStatus(500), "5xx keeps its status")
    expect(lookupError(.deepSeek, "not json", 200) == .malformedResponse, "non-JSON body is malformed")
    expect(lookupError(.moonshot, "[]", 200) == .malformedResponse, "a JSON array is not a balance object")
}

// MARK: - 8. Field helpers

do {
    expect(BalanceLookup.number(" 12.50 ") == 12.5, "numeric strings are trimmed and parsed")
    expect(BalanceLookup.number(true) == nil, "booleans are not numbers")
    expect(BalanceLookup.number(NSNumber(value: 3)) == 3, "JSON numbers are accepted")
    expect(BalanceLookup.number("abc") == nil, "non-numeric strings are rejected")
    expect(BalanceLookup.text(NSNumber(value: 7)) == "7", "numbers round-trip to text")
}

// MARK: - Result

if failures > 0 {
    FileHandle.standardError.write("FAILED \(failures)/\(checks) assertions\n".data(using: .utf8)!)
    exit(1)
}
print("PASS: \(checks) native balance assertions")
