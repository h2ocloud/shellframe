import Foundation
import WatchConnectivity

/// Watch side of the link: target list comes from the phone's applicationContext,
/// voice notes go to the phone as files, transcripts come back as messages.
@MainActor
final class PhoneLink: NSObject, ObservableObject, WCSessionDelegate {
    @Published var targets: [WatchTarget] = []
    @Published var selectedId: String? = UserDefaults.standard.string(forKey: "watch.target")
    @Published var pending: [String: Date] = [:]         // rid -> sent at
    @Published var results: [Result] = []
    @Published var phoneReachable = false

    struct Result: Identifiable { var id: String; var ok: Bool; var text: String; var at: Date }

    override init() {
        super.init()
        guard WCSession.isSupported() else { return }
        WCSession.default.delegate = self
        WCSession.default.activate()
        apply(context: WCSession.default.receivedApplicationContext)
    }

    var selected: WatchTarget? {
        targets.first { $0.id == selectedId } ?? targets.first
    }

    func select(_ t: WatchTarget) {
        selectedId = t.id
        UserDefaults.standard.set(t.id, forKey: "watch.target")
    }

    func refreshTargets() {
        guard WCSession.default.isReachable else { return }
        WCSession.default.sendMessage(["refresh": true], replyHandler: nil, errorHandler: nil)
    }

    /// Ship a recording to the phone; the phone posts it to the computer.
    func sendVoice(fileURL: URL, to target: WatchTarget) -> String {
        let rid = UUID().uuidString
        WCSession.default.transferFile(fileURL, metadata: [
            WatchKeys.kind: "voice", WatchKeys.requestId: rid,
            WatchKeys.peerId: target.peerId, WatchKeys.sid: target.sid,
        ])
        pending[rid] = Date()
        return rid
    }

    private func apply(context: [String: Any]) {
        guard let json = context[WatchKeys.targets] as? Data,
              let list = try? JSONDecoder().decode([WatchTarget].self, from: json) else { return }
        targets = list
        if selectedId == nil || !list.contains(where: { $0.id == selectedId }) { selectedId = list.first?.id }
    }

    private func handle(_ payload: [String: Any]) {
        guard payload[WatchKeys.kind] as? String == "voice" else { return }
        let rid = payload[WatchKeys.requestId] as? String ?? UUID().uuidString
        pending[rid] = nil
        let ok = (payload[WatchKeys.status] as? String) == "ok"
        let text = payload[WatchKeys.text] as? String ?? ""
        results.insert(Result(id: rid, ok: ok, text: text, at: Date()), at: 0)
        if results.count > 10 { results.removeLast(results.count - 10) }
    }

    // MARK: WCSessionDelegate

    nonisolated func session(_ session: WCSession, activationDidCompleteWith activationState: WCSessionActivationState, error: Error?) {
        Task { @MainActor in
            self.phoneReachable = session.isReachable
            self.apply(context: session.receivedApplicationContext)
        }
    }
    nonisolated func sessionReachabilityDidChange(_ session: WCSession) {
        Task { @MainActor in self.phoneReachable = session.isReachable }
    }
    nonisolated func session(_ session: WCSession, didReceiveApplicationContext applicationContext: [String: Any]) {
        Task { @MainActor in self.apply(context: applicationContext) }
    }
    nonisolated func session(_ session: WCSession, didReceiveMessage message: [String: Any]) {
        Task { @MainActor in self.handle(message) }
    }
    nonisolated func session(_ session: WCSession, didReceiveUserInfo userInfo: [String: Any] = [:]) {
        Task { @MainActor in self.handle(userInfo) }
    }
    nonisolated func session(_ session: WCSession, didFinish fileTransfer: WCSessionFileTransfer, error: Error?) {
        guard let error else { return }
        let rid = fileTransfer.file.metadata?[WatchKeys.requestId] as? String ?? UUID().uuidString
        Task { @MainActor in
            self.handle([WatchKeys.kind: "voice", WatchKeys.requestId: rid,
                         WatchKeys.status: "error", WatchKeys.text: "傳到手機失敗：\(error.localizedDescription)"])
        }
    }
}
