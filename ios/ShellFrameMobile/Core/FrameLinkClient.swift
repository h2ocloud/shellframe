import Foundation

/// Signed client for one paired peer. Picks a transport (direct first, relay as
/// fallback), signs every request the way `frame_link.py` expects and verifies
/// every response signature.
actor PeerConnection {
    let peer: Peer
    let myId: String
    private let secret: String
    private let transports: [LinkTransport]
    private var activeIndex: Int?
    private var lastProbe: Date = .distantPast
    private(set) var lastTransportLabel: String = ""

    /// Re-try the preferred (direct) transport this often while running on a fallback.
    private let reprobeInterval: TimeInterval = 45

    init(peer: Peer, secret: String, myId: String) {
        self.peer = peer
        self.secret = secret
        self.myId = myId
        var t: [LinkTransport] = peer.hosts.map { DirectTransport(host: $0, port: peer.port) }
        if let relay = peer.relay, !relay.url.isEmpty {
            t.append(RelayTransport(relay: relay, frameId: peer.id))
        }
        self.transports = t
    }

    var transportLabel: String { lastTransportLabel }

    // MARK: transport selection

    private func candidateOrder() -> [Int] {
        guard !transports.isEmpty else { return [] }
        var order = Array(transports.indices)
        if let a = activeIndex, a != 0, Date().timeIntervalSince(lastProbe) < reprobeInterval {
            // Stay on the working fallback; move it to the front.
            order.removeAll { $0 == a }
            order.insert(a, at: 0)
        }
        return order
    }

    private func raw(_ req: LinkRequest) async throws -> LinkResponse {
        guard !transports.isEmpty else { throw LinkError.unreachable("這台沒有可連的位址") }
        var lastErr: Error = LinkError.unreachable("no transport")
        for idx in candidateOrder() {
            let t = transports[idx]
            var r = req
            // Direct probes fail fast so falling back to the relay doesn't stall the UI.
            if idx != activeIndex, t is DirectTransport { r.timeout = min(r.timeout, 3) }
            do {
                let resp = try await t.perform(r)
                if activeIndex != idx { lastProbe = Date() }
                activeIndex = idx
                lastTransportLabel = t.label
                return resp
            } catch LinkError.cancelled {
                throw LinkError.cancelled
            } catch {
                lastErr = error
                if idx == activeIndex { activeIndex = nil }
                continue
            }
        }
        throw lastErr
    }

    // MARK: signing

    private func signed(_ method: String, _ pathQS: String, body: Data = Data(),
                        extraHeaders: [String: String] = [:], timeout: TimeInterval = 8,
                        signPayload: Data? = nil) async throws -> (LinkResponse, String) {
        let ts = LinkCrypto.timestampString()
        let nonce = LinkCrypto.randomHex()
        guard let sig = LinkCrypto.signRequest(secretHex: secret, method: method, pathQS: pathQS,
                                               ts: ts, nonce: nonce, body: signPayload ?? body) else {
            throw LinkError.noSecret
        }
        var headers = ["X-SF-Peer": myId, "X-SF-Ts": ts, "X-SF-Nonce": nonce, "X-SF-Sign": sig]
        if !body.isEmpty && extraHeaders["Content-Type"] == nil {
            headers["Content-Type"] = "application/json"
        }
        headers.merge(extraHeaders) { $1 }
        let resp = try await raw(LinkRequest(method: method, pathQS: pathQS, headers: headers,
                                             body: body.isEmpty ? nil : body, timeout: timeout))
        return (resp, nonce)
    }

    /// Signed JSON call with response-signature verification.
    private func call(_ method: String, _ pathQS: String, json: [String: Any]? = nil,
                      timeout: TimeInterval = 8) async throws -> [String: Any] {
        var body = Data()
        if let j = json { body = try JSONSerialization.data(withJSONObject: j) }
        let (resp, nonce) = try await signed(method, pathQS, body: body, timeout: timeout)
        let obj = (try? JSONSerialization.jsonObject(with: resp.body) as? [String: Any]) ?? [:]
        if resp.status != 200 {
            let msg = (obj["message"] as? String) ?? String(data: resp.body, encoding: .utf8) ?? ""
            throw LinkError.http(resp.status, msg)
        }
        guard let got = resp.header("x-sf-sign"),
              let want = LinkCrypto.expectedResponseSignature(secretHex: secret, nonce: nonce, body: resp.body),
              LinkCrypto.constantTimeEqual(got, want) else {
            throw LinkError.badSignature
        }
        return obj
    }

    // MARK: endpoints (mirror frame_link.py routes)

    func ping() async throws -> [String: Any] {
        try await call("GET", "/link/ping", timeout: 5)
    }

    struct Info { var frameName: String; var sessions: [RemoteSession]; var noControl: Bool }

    func info() async throws -> Info {
        let obj = try await call("GET", "/link/info")
        let details = obj["details"] as? [String: Any] ?? [:]
        let raw = details["sessions"] as? [[String: Any]] ?? []
        let data = try JSONSerialization.data(withJSONObject: raw)
        let sessions = (try? JSONDecoder().decode([RemoteSession].self, from: data)) ?? []
        return Info(frameName: obj["frame_name"] as? String ?? peer.name,
                    sessions: sessions,
                    noControl: (obj["no_control"] as? Bool) ?? false)
    }

    func peek(sid: String, lines: Int = 200) async throws -> String {
        let obj = try await call("GET", "/link/peek?sid=\(Self.q(sid))&lines=\(lines)")
        guard (obj["success"] as? Bool) == true else {
            throw LinkError.http(200, obj["message"] as? String ?? "peek failed")
        }
        return (obj["details"] as? [String: Any])?["text"] as? String ?? ""
    }

    /// Colour-faithful snapshot of the visible screen (`/link/snapshot`, v0.33+).
    /// Returns nil when the peer predates the route.
    func snapshot(sid: String) async throws -> String? {
        do {
            let obj = try await call("GET", "/link/snapshot?sid=\(Self.q(sid))")
            guard (obj["success"] as? Bool) == true else { return nil }
            return (obj["details"] as? [String: Any])?["ansi"] as? String
        } catch LinkError.http(404, _) {
            return nil
        }
    }

    func stream(sid: String, since: Int) async throws -> StreamChunk {
        let obj = try await call("GET", "/link/stream?sid=\(Self.q(sid))&since=\(since)", timeout: 6)
        guard (obj["success"] as? Bool) == true else {
            throw LinkError.http(200, obj["message"] as? String ?? "stream failed")
        }
        return StreamChunk(seq: obj["seq"] as? Int ?? since,
                           data: obj["data"] as? String ?? "",
                           reset: (obj["reset"] as? Bool) ?? false)
    }

    func input(sid: String, data: String) async throws {
        _ = try await call("POST", "/link/input", json: ["sid": sid, "data": data], timeout: 5)
    }

    func resize(sid: String, cols: Int, rows: Int) async throws {
        _ = try await call("POST", "/link/resize", json: ["sid": sid, "cols": cols, "rows": rows], timeout: 5)
    }

    func newSession(cmd: String) async throws -> String? {
        let obj = try await call("POST", "/link/new", json: ["cmd": cmd], timeout: 15)
        guard (obj["success"] as? Bool) == true else {
            throw LinkError.http(200, obj["message"] as? String ?? "new failed")
        }
        return (obj["details"] as? [String: Any])?["sid"] as? String
    }

    func closeSession(sid: String) async throws {
        let obj = try await call("POST", "/link/close", json: ["sid": sid], timeout: 10)
        guard (obj["success"] as? Bool) == true else {
            throw LinkError.http(200, obj["message"] as? String ?? "close failed")
        }
    }

    /// Bridge-quality text injection (tmux bracketed paste + optional Enter) — the
    /// same path Telegram messages take. Prefer this over raw input for prompts.
    func send(sid: String, text: String, submit: Bool = true) async throws {
        let obj = try await call("POST", "/link/send", json: ["sid": sid, "text": text, "submit": submit], timeout: 15)
        guard (obj["success"] as? Bool) == true else {
            throw LinkError.http(200, obj["message"] as? String ?? "send failed")
        }
    }

    func message(text: String) async throws {
        _ = try await call("POST", "/link/message", json: ["text": text])
    }

    /// Store-and-forward events the Mac queued for us (messages, etc.).
    func events(since: Int) async throws -> (cursor: Int, events: [[String: Any]]) {
        let obj = try await call("GET", "/link/events?since=\(since)", timeout: 6)
        return (obj["cursor"] as? Int ?? since, obj["events"] as? [[String: Any]] ?? [])
    }

    /// Agent RED/YELLOW signals (`/link/signals`, v0.33+). Empty on older peers.
    func signals(since: Int) async throws -> (cursor: Int, signals: [LinkSignal]) {
        do {
            let obj = try await call("GET", "/link/signals?since=\(since)", timeout: 6)
            let raw = obj["events"] as? [[String: Any]] ?? []
            let sigs = raw.compactMap { e -> LinkSignal? in
                guard let id = e["id"] as? Int else { return nil }
                return LinkSignal(id: id, sid: e["sid"] as? String ?? "", label: e["label"] as? String ?? "",
                                  state: e["state"] as? String ?? "", reason: e["reason"] as? String ?? "",
                                  ts: e["ts"] as? Double ?? 0)
            }
            return (obj["cursor"] as? Int ?? since, sigs)
        } catch LinkError.http(404, _) {
            return (since, [])
        }
    }

    /// Voice note → the Mac's STT chain → injected into `sid` (`/link/voice`, v0.33+).
    /// Signature covers the sha256 of the audio (same scheme as `/link/file`).
    func voice(sid: String, audio: Data, filename: String) async throws -> String {
        let digest = LinkCrypto.sha256Hex(audio)
        let path = "/link/voice?sid=\(Self.q(sid))"
        let (resp, nonce) = try await signed("POST", path, body: audio,
                                             extraHeaders: ["X-SF-Filename": filename,
                                                            "X-SF-Body-Sha256": digest,
                                                            "Content-Type": "application/octet-stream"],
                                             timeout: 120, signPayload: Data(digest.utf8))
        let obj = (try? JSONSerialization.jsonObject(with: resp.body) as? [String: Any]) ?? [:]
        if resp.status != 200 {
            throw LinkError.http(resp.status, obj["message"] as? String ?? "voice failed")
        }
        guard let got = resp.header("x-sf-sign"),
              let want = LinkCrypto.expectedResponseSignature(secretHex: secret, nonce: nonce, body: resp.body),
              LinkCrypto.constantTimeEqual(got, want) else { throw LinkError.badSignature }
        guard (obj["success"] as? Bool) == true else {
            throw LinkError.http(200, obj["message"] as? String ?? "voice failed")
        }
        return (obj["details"] as? [String: Any])?["text"] as? String ?? ""
    }

    static func q(_ s: String) -> String {
        s.addingPercentEncoding(withAllowedCharacters: .urlQueryValueAllowed) ?? s
    }
}

extension CharacterSet {
    /// Matches Python `urllib.parse.quote` defaults (safe "/", encode everything else non-unreserved).
    static let urlQueryValueAllowed: CharacterSet = {
        var s = CharacterSet.alphanumerics
        s.insert(charactersIn: "-._~/")
        return s
    }()
}

/// Joiner side of the pairing handshake (`FrameLink.join` in Python).
enum Pairing {
    struct Result { var peer: Peer; var secret: String }

    static func pair(payload: PairPayload, myId: String, myName: String) async throws -> Result {
        let code = LinkCrypto.normalizeCode(payload.code)
        guard code.count >= 8 else { throw LinkError.pairing("配對碑格式不對".replacingOccurrences(of: "碑", with: "碼")) }
        var transports: [LinkTransport] = payload.hosts.map { DirectTransport(host: $0, port: payload.port) }
        if let relay = payload.relay, !relay.url.isEmpty, let fid = payload.fid, !fid.isEmpty {
            transports.append(RelayTransport(relay: relay, frameId: fid))
        }
        guard !transports.isEmpty else { throw LinkError.pairing("沒有位址也沒有 relay，無法連到那台電腦") }

        var lastErr: Error = LinkError.unreachable("no transport")
        for t in transports {
            do {
                return try await handshake(via: t, payload: payload, code: code, myId: myId, myName: myName)
            } catch LinkError.pairing(let m) {
                throw LinkError.pairing(m)           // protocol-level refusal: don't retry elsewhere
            } catch LinkError.http(let c, let m) {
                throw LinkError.pairing(m.isEmpty ? "HTTP \(c)" : m)
            } catch {
                lastErr = error
            }
        }
        throw lastErr
    }

    private static func postJSON(_ t: LinkTransport, _ path: String, _ obj: [String: Any]) async throws -> [String: Any] {
        let body = try JSONSerialization.data(withJSONObject: obj)
        let resp = try await t.perform(LinkRequest(method: "POST", pathQS: path,
                                                   headers: ["Content-Type": "application/json"],
                                                   body: body, timeout: 8))
        let parsed = (try? JSONSerialization.jsonObject(with: resp.body) as? [String: Any]) ?? [:]
        if resp.status != 200 {
            throw LinkError.http(resp.status, parsed["message"] as? String ?? "")
        }
        return parsed
    }

    private static func handshake(via t: LinkTransport, payload: PairPayload, code: String,
                                  myId: String, myName: String) async throws -> Result {
        let joinerNonce = LinkCrypto.randomHex()
        let r1 = try await postJSON(t, "/link/pair/start", ["joiner_nonce": joinerNonce])
        guard (r1["success"] as? Bool) == true else {
            throw LinkError.pairing(r1["message"] as? String ?? "對方沒有開配對窗口")
        }
        guard let hostNonce = r1["host_nonce"] as? String, hostNonce.count >= 32 else {
            throw LinkError.pairing("handshake 回應異常")
        }
        let proof = LinkCrypto.pairProof(code: code, role: "join", joinerNonce: joinerNonce, hostNonce: hostNonce)
        let r2 = try await postJSON(t, "/link/pair/finish", [
            "joiner_nonce": joinerNonce,
            "proof": proof,
            "joiner_id": myId,
            "joiner_name": myName,
            "joiner_port": 0,             // phones are never reachable; the Mac never pushes to us
            "joiner_kind": "ios",
        ])
        guard (r2["success"] as? Bool) == true else {
            throw LinkError.pairing(r2["message"] as? String ?? "配對被拒絕")
        }
        let hostId = r2["host_id"] as? String ?? ""
        let wireMode = r2["mode"] as? String ?? "duplex"
        guard let mode = LinkMode.fromWire(wireMode) else {
            throw LinkError.pairing("未知的配對模式（版本不相容？）")
        }
        let expected = LinkCrypto.pairProof(code: code, role: "host", joinerNonce: joinerNonce,
                                            hostNonce: hostNonce, extra: wireMode)
        guard !hostId.isEmpty, LinkCrypto.constantTimeEqual(r2["proof"] as? String ?? "", expected) else {
            throw LinkError.pairing("對方無法證明持有配對碼（假冒端點或版本不相容）")
        }
        if let fid = payload.fid, !fid.isEmpty, fid != hostId {
            throw LinkError.pairing("回應的 frame_id 與 QR 不符")
        }
        let secret = LinkCrypto.deriveSecret(code: code, joinerNonce: joinerNonce, hostNonce: hostNonce,
                                             joinerId: myId, hostId: hostId)
        let hostPort = (r2["host_port"] as? Int) ?? payload.port
        let peer = Peer(id: hostId,
                        name: (r2["host_name"] as? String).flatMap { $0.isEmpty ? nil : $0 } ?? payload.name ?? payload.hosts.first ?? "ShellFrame",
                        hosts: payload.hosts,
                        port: hostPort,
                        relay: payload.relay,
                        mode: mode,
                        added: Date(),
                        lastSeen: Date())
        return Result(peer: peer, secret: secret)
    }
}
