import Foundation
import Combine
import WatchConnectivity

/// iPhone side of the watch link: publishes the list of tabs to the watch and
/// forwards the watch's voice notes to the right computer (`/link/voice`).
@MainActor
final class WatchBridge: NSObject, ObservableObject, WCSessionDelegate {
    @Published var lastWatchResult: String?
    private weak var store: PeerStore?
    private var cancellables: Set<AnyCancellable> = []
    private var lastContextJSON: Data?

    func attach(store: PeerStore) {
        guard self.store == nil else { return }
        self.store = store
        guard WCSession.isSupported() else { return }
        let s = WCSession.default
        s.delegate = self
        s.activate()
        store.$sessions
            .debounce(for: .seconds(1), scheduler: DispatchQueue.main)
            .sink { [weak self] _ in self?.pushTargets() }
            .store(in: &cancellables)
        store.$peers
            .sink { [weak self] _ in self?.pushTargets() }
            .store(in: &cancellables)
    }

    private func pushTargets() {
        guard let store, WCSession.isSupported(), WCSession.default.activationState == .activated,
              WCSession.default.isPaired else { return }
        var targets: [WatchTarget] = []
        for p in store.peers {
            for s in (store.sessions[p.id] ?? []) where s.isAlive {
                let prov = s.providerLabel.lowercased()
                targets.append(WatchTarget(peerId: p.id, peerName: p.name, sid: s.sid, label: s.label,
                                           isAI: prov.contains("claude") || prov.contains("codex") || prov.contains("gemini")))
            }
        }
        guard let json = try? JSONEncoder().encode(targets), json != lastContextJSON else { return }
        lastContextJSON = json
        try? WCSession.default.updateApplicationContext([WatchKeys.targets: json])
    }

    // MARK: WCSessionDelegate

    nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        Task { @MainActor in self.pushTargets() }
    }
    nonisolated func sessionDidBecomeInactive(_ session: WCSession) {}
    nonisolated func sessionDidDeactivate(_ session: WCSession) { session.activate() }
    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        Task { @MainActor in self.pushTargets() }
    }

    nonisolated func session(_ session: WCSession, didReceive file: WCSessionFile) {
        // The file is deleted once this returns — copy it out first.
        let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("watch-\(UUID().uuidString).m4a")
        try? FileManager.default.copyItem(at: file.fileURL, to: tmp)
        let meta = file.metadata ?? [:]
        Task { @MainActor in await self.handleVoice(fileURL: tmp, meta: meta) }
    }

    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any], replyHandler: @escaping ([String: Any]) -> Void) {
        // Watch asks for a fresh target list.
        Task { @MainActor in
            self.pushTargets()
            replyHandler([WatchKeys.status: "ok"])
        }
    }

    private func handleVoice(fileURL: URL, meta: [String: Any]) async {
        defer { try? FileManager.default.removeItem(at: fileURL) }
        let rid = meta[WatchKeys.requestId] as? String ?? UUID().uuidString
        guard let store, let peerId = meta[WatchKeys.peerId] as? String, let sid = meta[WatchKeys.sid] as? String,
              let conn = store.connection(for: peerId), let data = try? Data(contentsOf: fileURL) else {
            reply(rid: rid, status: "error", text: "手機找不到目標分頁，請重新配對或選分頁")
            return
        }
        do {
            let text = try await conn.voice(sid: sid, audio: data, filename: "watch.m4a")
            lastWatchResult = text
            reply(rid: rid, status: "ok", text: text)
        } catch {
            reply(rid: rid, status: "error", text: error.localizedDescription)
        }
    }

    private func reply(rid: String, status: String, text: String) {
        let payload: [String: Any] = [WatchKeys.kind: "voice", WatchKeys.requestId: rid,
                                      WatchKeys.status: status, WatchKeys.text: text]
        let s = WCSession.default
        if s.isReachable {
            s.sendMessage(payload, replyHandler: nil) { _ in s.transferUserInfo(payload) }
        } else {
            s.transferUserInfo(payload)
        }
    }
}
