import Foundation

/// Payloads exchanged between the iPhone app and the watch app over WatchConnectivity.
enum WatchKeys {
    static let targets = "targets"            // [WatchTarget] JSON in applicationContext
    static let peerId = "peerId"
    static let peerName = "peerName"
    static let sid = "sid"
    static let label = "label"
    static let kind = "kind"                  // "voice"
    static let status = "status"              // "ok" | "error"
    static let text = "text"
    static let requestId = "rid"
}

/// One selectable destination on the watch: a tab on a paired computer.
struct WatchTarget: Codable, Identifiable, Hashable {
    var peerId: String
    var peerName: String
    var sid: String
    var label: String
    var isAI: Bool

    var id: String { "\(peerId):\(sid)" }
}
