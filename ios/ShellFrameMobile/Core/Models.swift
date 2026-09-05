import Foundation

/// Relay (TG-style outbound long-poll tunnel) the Mac is registered with.
struct RelayConfig: Codable, Hashable {
    var url: String      // e.g. https://relay.example.com
    var token: String    // shared relay token (gates both the Mac's pull and our calls)

    var normalizedURL: String {
        var u = url.trimmingCharacters(in: .whitespacesAndNewlines)
        while u.hasSuffix("/") { u.removeLast() }
        return u
    }
}

/// Our view of a pairing: `duplex` both drive each other, `master` only we drive
/// the peer, `slave` only the peer drives us (so we can't see its tabs).
enum LinkMode: String, Codable {
    case duplex, master, slave

    /// Map the host's wire mode (host's perspective) onto the joiner's view.
    static func fromWire(_ wire: String) -> LinkMode? {
        switch wire {
        case "duplex": return .duplex
        case "host_controls": return .slave      // host drives us
        case "joiner_controls": return .master   // we drive host
        default: return nil
        }
    }

    var canControlPeer: Bool { self != .slave }
}

/// One paired computer (a ShellFrame instance). The HMAC secret lives in the
/// Keychain keyed by `id`; everything else is plain JSON on disk.
struct Peer: Codable, Identifiable, Hashable {
    var id: String            // the Mac's frame_id
    var name: String
    var hosts: [String]       // direct addresses to try, in order (LAN first)
    var port: Int
    var relay: RelayConfig?
    var mode: LinkMode
    var added: Date
    var lastSeen: Date?

    var hasDirect: Bool { !hosts.isEmpty && port > 0 }
}

/// A tab on the peer, as reported by `/link/info` (`list`).
struct RemoteSession: Codable, Identifiable, Hashable {
    var sid: String
    var label: String
    var cmd: String?
    var alive: Bool?
    var provider: String?
    var cols: Int?
    var rows: Int?
    var state: String?

    var id: String { sid }
    var isAlive: Bool { alive ?? true }
    var providerLabel: String {
        if let p = provider, !p.isEmpty { return p }
        return cmd?.split(separator: " ").first.map(String.init) ?? ""
    }
}

struct StreamChunk {
    var seq: Int
    var data: String
    var reset: Bool
}

struct LinkSignal: Identifiable, Hashable {
    var id: Int
    var sid: String
    var label: String
    var state: String
    var reason: String
    var ts: Double
}

/// What a pairing QR code / `shellframe://pair?d=…` link carries.
struct PairPayload: Codable, Hashable {
    var v: Int = 1
    var fid: String?          // host frame_id (needed to route through a relay)
    var name: String?
    var hosts: [String] = []
    var port: Int = 8767
    var code: String
    var mode: String?
    var relay: RelayConfig?

    static let scheme = "shellframe"

    /// Parse `shellframe://pair?d=<base64url(json)>`.
    static func parse(url: URL) -> PairPayload? {
        guard url.scheme?.lowercased() == scheme,
              (url.host ?? "").lowercased() == "pair" else { return nil }
        let comps = URLComponents(url: url, resolvingAgainstBaseURL: false)
        guard let d = comps?.queryItems?.first(where: { $0.name == "d" })?.value,
              let json = base64URLDecode(d) else { return nil }
        return try? JSONDecoder().decode(PairPayload.self, from: json)
    }

    static func parse(text: String) -> PairPayload? {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if let u = URL(string: t), let p = parse(url: u) { return p }
        return nil
    }

    func encodedURL() -> URL? {
        guard let json = try? JSONEncoder().encode(self) else { return nil }
        var s = json.base64EncodedString()
        s = s.replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_")
        while s.hasSuffix("=") { s.removeLast() }
        return URL(string: "\(PairPayload.scheme)://pair?d=\(s)")
    }

    static func base64URLDecode(_ s: String) -> Data? {
        var b = s.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while b.count % 4 != 0 { b.append("=") }
        return Data(base64Encoded: b)
    }
}

enum LinkError: LocalizedError {
    case unreachable(String)
    case relay(String)
    case http(Int, String)
    case badSignature
    case pairing(String)
    case decode(String)
    case noSecret
    case cancelled

    var errorDescription: String? {
        switch self {
        case .unreachable(let m): return "連不上：\(m)"
        case .relay(let m): return "Relay：\(m)"
        case .http(let code, let m): return m.isEmpty ? "HTTP \(code)" : m
        case .badSignature: return "回應簽章不符（不是配對的那台？）"
        case .pairing(let m): return m
        case .decode(let m): return "回應格式異常：\(m)"
        case .noSecret: return "找不到這台的配對金鑰，請重新配對"
        case .cancelled: return "已取消"
        }
    }
}
