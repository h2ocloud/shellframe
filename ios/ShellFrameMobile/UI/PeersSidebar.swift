import SwiftUI

struct PeersSidebar: View {
    @EnvironmentObject var store: PeerStore
    @Binding var selection: SessionRef?
    @Binding var showPairing: Bool
    @Binding var showSettings: Bool
    @State private var confirmUnpair: Peer?
    @State private var editPeer: Peer?
    @State private var newSessionPeer: Peer?
    @State private var busy: String?

    var body: some View {
        List(selection: $selection) {
            if store.peers.isEmpty {
                ContentUnavailableView("尚未配對", systemImage: "desktopcomputer",
                                       description: Text("按右上角 ＋ 掃描電腦上的配對 QR code。"))
            }
            ForEach(store.peers) { peer in
                Section {
                    let list = store.sessions[peer.id] ?? []
                    if store.noControl[peer.id] == true {
                        Label("單向配對：這台電腦不開放操作", systemImage: "lock")
                            .font(.footnote).foregroundStyle(.secondary)
                    } else if list.isEmpty {
                        Text(store.reachable[peer.id] == false ? "連不上" : "沒有 session")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    ForEach(Array(list.enumerated()), id: \.element.id) { idx, s in
                        SessionRow(session: s, number: idx + 1,
                                   needsAttention: store.attention[peer.id]?.contains(s.sid) == true)
                            .tag(SessionRef(peerId: peer.id, sid: s.sid))
                            .contextMenu {
                                Button(role: .destructive) {
                                    Task { try? await store.connection(for: peer.id)?.closeSession(sid: s.sid); await store.refresh(peerId: peer.id) }
                                } label: { Label("關閉這個 session", systemImage: "xmark.circle") }
                            }
                    }
                } header: {
                    PeerHeader(peer: peer,
                               reachable: store.reachable[peer.id],
                               transport: store.transport[peer.id] ?? "",
                               error: store.lastError[peer.id],
                               onNew: { newSessionPeer = peer },
                               onEdit: { editPeer = peer },
                               onUnpair: { confirmUnpair = peer },
                               onRefresh: { Task { await store.refresh(peerId: peer.id) } })
                }
            }
        }
        .listStyle(.sidebar)
        .refreshable { await store.refreshAll() }
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button { showPairing = true } label: { Image(systemName: "plus") }
                    .accessibilityLabel("配對電腦")
            }
            ToolbarItem(placement: .navigation) {
                Button { showSettings = true } label: { Image(systemName: "gearshape") }
            }
        }
        .confirmationDialog("斷開與「\(confirmUnpair?.name ?? "")」的配對？",
                            isPresented: Binding(get: { confirmUnpair != nil }, set: { if !$0 { confirmUnpair = nil } }),
                            titleVisibility: .visible) {
            Button("斷開", role: .destructive) {
                if let p = confirmUnpair {
                    if selection?.peerId == p.id { selection = nil }
                    store.remove(p)
                }
                confirmUnpair = nil
            }
        } message: {
            Text("會刪除這支手機儲存的配對金鑰。電腦那邊的記錄要在電腦上移除。")
        }
        .sheet(item: $editPeer) { p in EditPeerSheet(peer: p).environmentObject(store) }
        .sheet(item: $newSessionPeer) { p in
            NewSessionSheet(peer: p) { sid in
                if let sid { selection = SessionRef(peerId: p.id, sid: sid) }
            }.environmentObject(store)
        }
    }
}

struct PeerHeader: View {
    var peer: Peer
    var reachable: Bool?
    var transport: String
    var error: String?
    var onNew: () -> Void
    var onEdit: () -> Void
    var onUnpair: () -> Void
    var onRefresh: () -> Void

    var body: some View {
        HStack(spacing: 8) {
            Circle()
                .fill(reachable == true ? Color.green : (reachable == false ? Color.red : Color.gray))
                .frame(width: 8, height: 8)
            VStack(alignment: .leading, spacing: 1) {
                Text(peer.name).font(.subheadline.weight(.semibold)).textCase(nil)
                Text(subtitle).font(.caption2).foregroundStyle(.secondary).textCase(nil).lineLimit(1)
            }
            Spacer()
            Menu {
                Button(action: onNew) { Label("新增 session", systemImage: "plus.rectangle") }
                Button(action: onRefresh) { Label("重新整理", systemImage: "arrow.clockwise") }
                Button(action: onEdit) { Label("編輯位址／relay", systemImage: "pencil") }
                Divider()
                Button(role: .destructive, action: onUnpair) { Label("斷開配對", systemImage: "link.badge.plus") }
            } label: {
                Image(systemName: "ellipsis.circle").imageScale(.medium)
            }
        }
    }

    private var subtitle: String {
        if reachable == false { return error ?? "連不上" }
        var parts: [String] = []
        if transport.hasPrefix("relay") { parts.append("經 relay") }
        else if !transport.isEmpty { parts.append(transport.replacingOccurrences(of: "direct ", with: "")) }
        switch peer.mode {
        case .master: parts.append("這支手機是主控")
        case .slave: parts.append("只能看訊息")
        case .duplex: break
        }
        return parts.joined(separator: " · ")
    }
}

struct SessionRow: View {
    var session: RemoteSession
    var number: Int            // same 1-based order as the desktop sidebar
    var needsAttention: Bool

    var body: some View {
        HStack(spacing: 8) {
            Text("\(number)")
                .font(.caption.monospacedDigit())
                .foregroundStyle(.secondary)
                .frame(minWidth: 18, alignment: .trailing)
            Image(systemName: icon)
                .foregroundStyle(session.isAlive ? Color.accentColor : Color.secondary)
                .frame(width: 18)
            VStack(alignment: .leading, spacing: 1) {
                Text(session.label).lineLimit(1)
                HStack(spacing: 6) {
                    Text(session.sid).font(.caption2).foregroundStyle(.secondary)
                    if let c = session.cols, let r = session.rows, c > 0 {
                        Text("\(c)×\(r)").font(.caption2).foregroundStyle(.secondary)
                    }
                    if !session.providerLabel.isEmpty {
                        Text(session.providerLabel).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
            Spacer()
            if needsAttention {
                Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.orange)
            }
            if !session.isAlive {
                Text("已結束").font(.caption2).foregroundStyle(.secondary)
            }
        }
    }

    private var icon: String {
        switch session.providerLabel.lowercased() {
        case let p where p.contains("claude"): return "sparkles"
        case let p where p.contains("codex"): return "cpu"
        case let p where p.contains("bash") || p.contains("zsh") || p.contains("sh"): return "terminal"
        default: return "rectangle.on.rectangle"
        }
    }
}

struct NewSessionSheet: View {
    @EnvironmentObject var store: PeerStore
    @Environment(\.dismiss) private var dismiss
    var peer: Peer
    var onCreated: (String?) -> Void
    @State private var custom = ""
    @State private var working = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                Section("在「\(peer.name)」開新 session") {
                    Button { create("claude") } label: { Label("Claude", systemImage: "sparkles") }
                    Button { create("sf-codex --dangerously-bypass-approvals-and-sandbox --search --no-alt-screen") } label: { Label("Codex", systemImage: "cpu") }
                    Button { create("bash") } label: { Label("bash", systemImage: "terminal") }
                }
                Section("自訂指令") {
                    TextField("例如 claude --model opus", text: $custom)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("執行") { create(custom) }.disabled(custom.trimmingCharacters(in: .whitespaces).isEmpty)
                }
                if let e = error { Text(e).foregroundStyle(.red).font(.footnote) }
            }
            .disabled(working)
            .navigationTitle("新增 session")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } } }
        }
        .presentationDetents([.medium])
    }

    private func create(_ cmd: String) {
        working = true
        Task {
            do {
                let sid = try await store.connection(for: peer.id)?.newSession(cmd: cmd)
                await store.refresh(peerId: peer.id)
                dismiss()
                onCreated(sid)
            } catch {
                self.error = error.localizedDescription
            }
            working = false
        }
    }
}

struct EditPeerSheet: View {
    @EnvironmentObject var store: PeerStore
    @Environment(\.dismiss) private var dismiss
    @State var peer: Peer
    @State private var hostsText: String = ""
    @State private var portText: String = ""
    @State private var relayURL: String = ""
    @State private var relayToken: String = ""

    var body: some View {
        NavigationStack {
            Form {
                Section("直連位址（IP 或網域，可多個，逗號分隔）") {
                    TextField("192.168.1.10, my.ddns.net", text: $hostsText)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    TextField("port", text: $portText).keyboardType(.numberPad)
                }
                Section("Relay（公網用，電腦與手機都出站連 relay）") {
                    TextField("https://relay.example.com", text: $relayURL)
                        .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                    TextField("relay token", text: $relayToken)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                }
                Section { Text("frame_id：\(peer.id)").font(.caption2).foregroundStyle(.secondary) }
            }
            .navigationTitle("編輯「\(peer.name)」")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) { Button("儲存") { save() } }
            }
            .onAppear {
                hostsText = peer.hosts.joined(separator: ", ")
                portText = String(peer.port)
                relayURL = peer.relay?.url ?? ""
                relayToken = peer.relay?.token ?? ""
            }
        }
    }

    private func save() {
        var p = peer
        p.hosts = hostsText.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        p.port = Int(portText.trimmingCharacters(in: .whitespaces)) ?? 8767
        let u = relayURL.trimmingCharacters(in: .whitespaces)
        p.relay = u.isEmpty ? nil : RelayConfig(url: u, token: relayToken.trimmingCharacters(in: .whitespaces))
        store.update(p)
        Task { await store.refresh(peerId: p.id) }
        dismiss()
    }
}
