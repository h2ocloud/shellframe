import SwiftUI
import VisionKit

/// Pair with a computer: scan the QR the desktop shows, tap a `shellframe://pair` link,
/// or type host/port/code by hand (same as the desktop's「加入配對」).
struct PairingView: View {
    @EnvironmentObject var store: PeerStore
    @Environment(\.dismiss) private var dismiss
    /// Payload handed in by a deep link / QA hook. A binding (not a value) so the
    /// sheet never captures a stale nil when state changes in the same transaction.
    @Binding var incoming: PairPayload?

    @State private var payload: PairPayload?
    @State private var host = ""
    @State private var port = "8767"
    @State private var code = ""
    @State private var pasted = ""
    @State private var working = false
    @State private var error: String?
    @State private var scanning = true

    private var scannerAvailable: Bool {
        DataScannerViewController.isSupported && DataScannerViewController.isAvailable
    }

    var body: some View {
        NavigationStack {
            Form {
                if let p = payload {
                    Section("已讀到配對資訊") {
                        LabeledContent("電腦", value: p.name ?? p.fid?.prefix(8).description ?? "?")
                        if !p.hosts.isEmpty { LabeledContent("位址", value: p.hosts.joined(separator: ", ") + ":\(p.port)") }
                        if let r = p.relay, !r.url.isEmpty { LabeledContent("Relay", value: r.normalizedURL) }
                        LabeledContent("配對碼", value: p.code)
                        if working {
                            HStack { ProgressView(); Text("配對中…").foregroundStyle(.secondary) }
                        } else {
                            Button("再試一次") { pair(p) }
                            Button("重新掃描", role: .cancel) { payload = nil; scanning = true; error = nil }
                        }
                    }
                } else {
                    if scannerAvailable && scanning {
                        Section("掃描電腦上的 QR code") {
                            QRScanner { text in
                                if let p = PairPayload.parse(text: text) {
                                    payload = p
                                    scanning = false
                                }
                            }
                            .frame(height: 260)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                            .listRowInsets(EdgeInsets())
                        }
                    }
                    Section("或貼上配對連結") {
                        TextField("shellframe://pair?d=…", text: $pasted)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                            .onSubmit { applyPasted() }
                        Button("使用連結") { applyPasted() }.disabled(pasted.isEmpty)
                    }
                    Section("或手動輸入（區網／已 port-forward）") {
                        TextField("電腦 IP 或網域", text: $host)
                            .textInputAutocapitalization(.never).autocorrectionDisabled().keyboardType(.URL)
                        TextField("port", text: $port).keyboardType(.numberPad)
                        TextField("配對碼（例 K7QX2-MRD34）", text: $code)
                            .textInputAutocapitalization(.characters).autocorrectionDisabled()
                            .font(.body.monospaced())
                        Button(working ? "配對中…" : "配對") {
                            let p = PairPayload(fid: nil, name: host, hosts: [host.trimmingCharacters(in: .whitespaces)],
                                                port: Int(port) ?? 8767, code: code, mode: nil, relay: nil)
                            pair(p)
                        }
                        .disabled(working || host.trimmingCharacters(in: .whitespaces).isEmpty || code.count < 8)
                    }
                }
                if let e = error {
                    Section { Text(e).foregroundStyle(.red).font(.footnote) }
                }
                Section {
                    Text("配對碼只用一次、120 秒內有效。配對後雙方各自持有一把金鑰，之後不用再輸入任何東西。這支裝置的 ID：\(store.myId.prefix(8))…")
                        .font(.caption2).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("配對電腦")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("關閉") { dismiss() } } }
            .onAppear { if let p = incoming { payload = p; scanning = false } }
            .onChange(of: incoming) { _, p in if let p { payload = p; scanning = false } }
            // A scanned QR or a tapped shellframe:// link already expresses intent: pair right away.
            .onChange(of: payload) { _, p in
                if let p, !working { pair(p) }
            }
        }
    }

    private func applyPasted() {
        if let p = PairPayload.parse(text: pasted) { payload = p; error = nil; scanning = false }
        else { error = "看不懂這個連結" }
    }

    private func pair(_ p: PairPayload) {
        working = true
        error = nil
        Task {
            do {
                let result = try await Pairing.pair(payload: p, myId: store.myId, myName: DeviceIdentity.deviceName)
                store.add(result)
                dismiss()
            } catch {
                self.error = error.localizedDescription
            }
            working = false
        }
    }
}

/// VisionKit live QR scanner (iOS 16+). Not available on the simulator.
struct QRScanner: UIViewControllerRepresentable {
    var onCode: (String) -> Void

    func makeUIViewController(context: Context) -> DataScannerViewController {
        let vc = DataScannerViewController(recognizedDataTypes: [.barcode(symbologies: [.qr])],
                                           qualityLevel: .balanced,
                                           recognizesMultipleItems: false,
                                           isHighFrameRateTrackingEnabled: false,
                                           isHighlightingEnabled: true)
        vc.delegate = context.coordinator
        try? vc.startScanning()
        return vc
    }

    func updateUIViewController(_ uiViewController: DataScannerViewController, context: Context) {}

    static func dismantleUIViewController(_ vc: DataScannerViewController, coordinator: Coordinator) {
        vc.stopScanning()
    }

    func makeCoordinator() -> Coordinator { Coordinator(onCode: onCode) }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let onCode: (String) -> Void
        private var fired = false
        init(onCode: @escaping (String) -> Void) { self.onCode = onCode }

        func dataScanner(_ dataScanner: DataScannerViewController, didAdd addedItems: [RecognizedItem], allItems: [RecognizedItem]) {
            guard !fired else { return }
            for item in addedItems {
                if case .barcode(let b) = item, let s = b.payloadStringValue, PairPayload.parse(text: s) != nil {
                    fired = true
                    onCode(s)
                    return
                }
            }
        }
    }
}
