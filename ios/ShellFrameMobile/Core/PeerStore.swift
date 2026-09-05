import Foundation
import Combine

/// Identity of this device as a Frame Link peer.
enum DeviceIdentity {
    private static let idKey = "sf.frame_id"
    private static let nameKey = "sf.device_name"

    static var frameId: String {
        if let v = UserDefaults.standard.string(forKey: idKey), !v.isEmpty { return v }
        let v = LinkCrypto.randomHex(bytes: 16)      // 32 hex, same shape as the Mac's uuid4().hex
        UserDefaults.standard.set(v, forKey: idKey)
        return v
    }

    static var deviceName: String {
        get {
            if let v = UserDefaults.standard.string(forKey: nameKey), !v.isEmpty { return v }
            #if canImport(UIKit)
            return UIDevice.current.name
            #else
            return "iPhone"
            #endif
        }
        set { UserDefaults.standard.set(newValue, forKey: nameKey) }
    }
}

#if canImport(UIKit)
import UIKit
#endif

/// Persisted list of paired computers + live per-peer state (sessions, reachability).
@MainActor
final class PeerStore: ObservableObject {
    @Published private(set) var peers: [Peer] = []
    @Published var sessions: [String: [RemoteSession]] = [:]
    @Published var reachable: [String: Bool] = [:]
    @Published var transport: [String: String] = [:]
    @Published var lastError: [String: String] = [:]
    @Published var noControl: [String: Bool] = [:]
    @Published var attention: [String: Set<String>] = [:]     // peerId -> sids with RED/YELLOW

    let myId = DeviceIdentity.frameId
    private var connections: [String: PeerConnection] = [:]
    private var signalCursor: [String: Int] = [:]
    private var refreshTask: Task<Void, Never>?

    private static var fileURL: URL {
        let dir = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ShellFrame", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir.appendingPathComponent("peers.json")
    }

    init() { load() }

    // MARK: persistence

    private func load() {
        guard let data = try? Data(contentsOf: Self.fileURL),
              let list = try? JSONDecoder().decode([Peer].self, from: data) else { return }
        peers = list
    }

    private func save() {
        if let data = try? JSONEncoder().encode(peers) {
            try? data.write(to: Self.fileURL, options: .atomic)
        }
    }

    // MARK: peers

    func add(_ result: Pairing.Result) {
        Keychain.set(result.secret, account: result.peer.id)
        connections[result.peer.id] = nil
        if let i = peers.firstIndex(where: { $0.id == result.peer.id }) {
            peers[i] = result.peer
        } else {
            peers.append(result.peer)
        }
        save()
        Task { await refresh(peerId: result.peer.id) }
    }

    func remove(_ peer: Peer) {
        Keychain.delete(account: peer.id)
        connections[peer.id] = nil
        peers.removeAll { $0.id == peer.id }
        sessions[peer.id] = nil
        reachable[peer.id] = nil
        save()
    }

    func update(_ peer: Peer) {
        guard let i = peers.firstIndex(where: { $0.id == peer.id }) else { return }
        peers[i] = peer
        connections[peer.id] = nil        // transports changed
        save()
    }

    func peer(_ id: String) -> Peer? { peers.first { $0.id == id } }

    func connection(for peerId: String) -> PeerConnection? {
        if let c = connections[peerId] { return c }
        guard let p = peer(peerId), let secret = Keychain.get(account: peerId) else { return nil }
        let c = PeerConnection(peer: p, secret: secret, myId: myId)
        connections[peerId] = c
        return c
    }

    // MARK: live state

    func refresh(peerId: String) async {
        guard let conn = connection(for: peerId) else {
            lastError[peerId] = LinkError.noSecret.localizedDescription
            reachable[peerId] = false
            sfLog.error("refresh \(peerId, privacy: .public): no connection (secret missing)")
            return
        }
        do {
            let info = try await conn.info()
            let via = await conn.transportLabel
            sfLog.info("refresh \(peerId, privacy: .public): \(info.sessions.count) sessions via \(via, privacy: .public)")
            sessions[peerId] = info.sessions
            noControl[peerId] = info.noControl
            reachable[peerId] = true
            lastError[peerId] = nil
            transport[peerId] = await conn.transportLabel
            if var p = peer(peerId) {
                var changed = false
                if p.name != info.frameName, !info.frameName.isEmpty { p.name = info.frameName; changed = true }
                p.lastSeen = Date()
                if let i = peers.firstIndex(where: { $0.id == peerId }) { peers[i] = p }
                if changed { save() }
            }
            let (cursor, sigs) = try await conn.signals(since: signalCursor[peerId] ?? 0)
            signalCursor[peerId] = cursor
            if !sigs.isEmpty {
                var set = attention[peerId] ?? []
                for s in sigs {
                    if s.state == "RED" || s.state == "YELLOW" { set.insert(s.sid) } else { set.remove(s.sid) }
                }
                attention[peerId] = set
            }
        } catch {
            reachable[peerId] = false
            lastError[peerId] = error.localizedDescription
            sfLog.error("refresh \(peerId, privacy: .public) failed: \(error.localizedDescription, privacy: .public)")
        }
    }

    func refreshAll() async {
        await withTaskGroup(of: Void.self) { group in
            for p in peers { group.addTask { await self.refresh(peerId: p.id) } }
        }
    }

    func clearAttention(peerId: String, sid: String) {
        attention[peerId]?.remove(sid)
    }

    /// Periodic sidebar refresh while the app is in the foreground.
    func startAutoRefresh(interval: TimeInterval = 5) {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refreshAll()
                try? await Task.sleep(nanoseconds: UInt64(interval * 1_000_000_000))
            }
        }
    }

    func stopAutoRefresh() {
        refreshTask?.cancel()
        refreshTask = nil
    }
}
