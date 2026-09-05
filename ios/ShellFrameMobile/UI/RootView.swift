import SwiftUI

/// A tab on a peer, used as the navigation selection.
struct SessionRef: Hashable, Identifiable {
    var peerId: String
    var sid: String
    var id: String { "\(peerId):\(sid)" }
}

struct RootView: View {
    @EnvironmentObject var store: PeerStore
    @State private var selection: SessionRef?
    @State private var pairPayload: PairPayload?
    @State private var showPairing = false
    @State private var showSettings = false
    @State private var columnVisibility: NavigationSplitViewVisibility = .automatic

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            PeersSidebar(selection: $selection, showPairing: $showPairing, showSettings: $showSettings)
                .navigationTitle("ShellFrame")
        } detail: {
            if let sel = selection, let peer = store.peer(sel.peerId) {
                TerminalScreen(ref: sel, peer: peer)
                    .id(sel.id)
            } else {
                EmptyDetail(hasPeers: !store.peers.isEmpty, showPairing: $showPairing)
            }
        }
        .sheet(isPresented: $showPairing, onDismiss: { pairPayload = nil }) {
            PairingView(initialPayload: pairPayload)
                .environmentObject(store)
        }
        .sheet(isPresented: $showSettings) {
            SettingsView().environmentObject(store)
        }
        .onOpenURL { url in
            if let p = PairPayload.parse(url: url) {
                pairPayload = p
                showPairing = true
            }
        }
        // QA hook (simulator only): SF_QA_AUTOSELECT=1 opens the first live session once
        // the peer list loads, so screenshots can be driven without tapping.
        .onChange(of: store.sessions) { _, all in
            guard selection == nil, QAHooks.autoselect else { return }
            for p in store.peers {
                if let s = (all[p.id] ?? []).first(where: { $0.isAlive }) {
                    selection = SessionRef(peerId: p.id, sid: s.sid)
                    return
                }
            }
        }
    }
}

enum QAHooks {
    static var autoselect: Bool { ProcessInfo.processInfo.environment["SF_QA_AUTOSELECT"] == "1" }
    /// "1" / "0" forces fit mode for screenshots; unset = device default.
    static var fitOverride: Bool? {
        switch ProcessInfo.processInfo.environment["SF_QA_FIT"] {
        case "1": return true
        case "0": return false
        default: return nil
        }
    }
}

struct EmptyDetail: View {
    var hasPeers: Bool
    @Binding var showPairing: Bool

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "terminal")
                .font(.system(size: 44))
                .foregroundStyle(.secondary)
            if hasPeers {
                Text("從左側選一個 session").font(.headline)
                Text("畫面會即時鏡射電腦上的終端，鍵盤直接打進去。")
                    .font(.footnote).foregroundStyle(.secondary)
            } else {
                Text("尚未配對任何電腦").font(.headline)
                Text("在電腦的 ShellFrame 側欄按「＋ 配對」產生 QR code，再用這裡掃描。")
                    .font(.footnote).foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                Button { showPairing = true } label: {
                    Label("配對電腦", systemImage: "qrcode.viewfinder")
                }
                .buttonStyle(.borderedProminent)
            }
        }
        .padding()
    }
}
