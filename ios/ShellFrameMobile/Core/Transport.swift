import Foundation

struct LinkRequest {
    var method: String
    var pathQS: String                // path + query, byte-identical to what gets signed
    var headers: [String: String] = [:]
    var body: Data? = nil
    var timeout: TimeInterval = 8
}

struct LinkResponse {
    var status: Int
    var headers: [String: String]     // lower-cased keys
    var body: Data

    func header(_ name: String) -> String? { headers[name.lowercased()] }
}

/// How signed envelopes reach a peer: straight to its listener, or through a relay.
protocol LinkTransport: AnyObject {
    var label: String { get }
    func perform(_ req: LinkRequest) async throws -> LinkResponse
}

enum TransportSession {
    /// One ephemeral session for everything: no cookies, no cache, short timeouts.
    static let shared: URLSession = {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.timeoutIntervalForRequest = 30
        cfg.timeoutIntervalForResource = 120
        cfg.waitsForConnectivity = false
        cfg.httpMaximumConnectionsPerHost = 6
        return URLSession(configuration: cfg)
    }()

    static func lowercased(_ resp: HTTPURLResponse) -> [String: String] {
        var out: [String: String] = [:]
        for (k, v) in resp.allHeaderFields {
            if let ks = k as? String, let vs = v as? String { out[ks.lowercased()] = vs }
        }
        return out
    }
}

/// Direct HTTP to `http://host:port` (LAN, VPN, or a port-forwarded public address).
final class DirectTransport: LinkTransport {
    let host: String
    let port: Int
    var label: String { "direct \(host):\(port)" }

    init(host: String, port: Int) { self.host = host; self.port = port }

    private var base: String {
        // Bracket bare IPv6 literals.
        if host.contains(":") && !host.hasPrefix("[") { return "http://[\(host)]:\(port)" }
        return "http://\(host):\(port)"
    }

    func perform(_ req: LinkRequest) async throws -> LinkResponse {
        guard let url = URL(string: base + req.pathQS) else {
            throw LinkError.unreachable("bad url \(base)\(req.pathQS)")
        }
        var r = URLRequest(url: url, timeoutInterval: req.timeout)
        r.httpMethod = req.method
        for (k, v) in req.headers { r.setValue(v, forHTTPHeaderField: k) }
        r.httpBody = req.body
        do {
            let (data, resp) = try await TransportSession.shared.data(for: r)
            guard let http = resp as? HTTPURLResponse else { throw LinkError.unreachable("no http response") }
            return LinkResponse(status: http.statusCode, headers: TransportSession.lowercased(http), body: data)
        } catch let e as LinkError {
            throw e
        } catch {
            if (error as? URLError)?.code == .cancelled { throw LinkError.cancelled }
            throw LinkError.unreachable("\(host):\(port) — \(error.localizedDescription)")
        }
    }
}

/// TG-style relay: we POST an envelope, the Mac long-polls the relay and replays it
/// against its own Frame Link listener, then posts the answer back. The relay only
/// ever sees signed envelopes it cannot forge (it does see plaintext — documented).
final class RelayTransport: LinkTransport {
    let relay: RelayConfig
    let frameId: String
    var label: String { "relay \(relay.normalizedURL)" }

    init(relay: RelayConfig, frameId: String) { self.relay = relay; self.frameId = frameId }

    func perform(_ req: LinkRequest) async throws -> LinkResponse {
        guard let url = URL(string: "\(relay.normalizedURL)/r/\(frameId)/call") else {
            throw LinkError.relay("bad relay url")
        }
        var envelope: [String: Any] = [
            "method": req.method,
            "path": req.pathQS,
            "headers": req.headers,
            "wait": Int(max(5, min(60, req.timeout + 4))),
        ]
        if let b = req.body { envelope["body_b64"] = b.base64EncodedString() }
        var r = URLRequest(url: url, timeoutInterval: req.timeout + 12)
        r.httpMethod = "POST"
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.setValue("Bearer \(relay.token)", forHTTPHeaderField: "Authorization")
        r.httpBody = try JSONSerialization.data(withJSONObject: envelope)
        let data: Data
        let http: HTTPURLResponse
        do {
            let (d, resp) = try await TransportSession.shared.data(for: r)
            guard let h = resp as? HTTPURLResponse else { throw LinkError.relay("no http response") }
            data = d; http = h
        } catch let e as LinkError {
            throw e
        } catch {
            if (error as? URLError)?.code == .cancelled { throw LinkError.cancelled }
            throw LinkError.relay(error.localizedDescription)
        }
        switch http.statusCode {
        case 200: break
        case 401, 403: throw LinkError.relay("relay token 不對")
        case 404: throw LinkError.relay("relay 不認識這台電腦（尚未註冊）")
        case 504: throw LinkError.relay("電腦沒有連上 relay（離線？）")
        default:
            let msg = String(data: data, encoding: .utf8) ?? ""
            throw LinkError.relay("HTTP \(http.statusCode) \(msg.prefix(120))")
        }
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let status = obj["status"] as? Int else {
            throw LinkError.relay("回應格式異常")
        }
        var headers: [String: String] = [:]
        if let h = obj["headers"] as? [String: String] {
            for (k, v) in h { headers[k.lowercased()] = v }
        }
        let body = (obj["body_b64"] as? String).flatMap { Data(base64Encoded: $0) } ?? Data()
        return LinkResponse(status: status, headers: headers, body: body)
    }
}
