import SwiftUI
import UIKit
import SwiftTerm

/// One remote tab, rendered by SwiftTerm and fed from `/link/stream`.
struct TerminalScreen: View {
    @EnvironmentObject var store: PeerStore
    @EnvironmentObject var watch: WatchBridge
    let ref: SessionRef
    let peer: Peer
    @StateObject private var model: TerminalModel
    @State private var showComposer = false
    @State private var showVoice = false

    init(ref: SessionRef, peer: Peer) {
        self.ref = ref
        self.peer = peer
        _model = StateObject(wrappedValue: TerminalModel(ref: ref))
    }

    private var session: RemoteSession? {
        store.sessions[ref.peerId]?.first { $0.sid == ref.sid }
    }

    var body: some View {
        ZStack(alignment: .top) {
            TerminalHost(model: model)
                .ignoresSafeArea(.container, edges: .bottom)
                .accessibilityIdentifier("terminal")
            if let s = model.status {
                Text(s)
                    .font(.caption)
                    .padding(.horizontal, 10).padding(.vertical, 5)
                    .background(.ultraThinMaterial, in: Capsule())
                    .padding(.top, 6)
                    .transition(.opacity)
            }
        }
        .background(Color(red: 0x1a / 255, green: 0x1b / 255, blue: 0x26 / 255))
        .navigationTitle(session?.label ?? ref.sid)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { model.toggleKeyboard() } label: { Image(systemName: "keyboard") }
                    .help("顯示／收起鍵盤")
                    .accessibilityIdentifier("keyboardButton")
                Button { model.fitMode.toggle() } label: {
                    Image(systemName: model.fitMode ? "arrow.down.right.and.arrow.up.left" : "arrow.up.left.and.arrow.down.right")
                }
                .help(model.fitMode ? "目前：撐滿這台裝置（會改電腦上的終端尺寸）" : "目前：照電腦尺寸顯示")
                Button { showVoice = true } label: { Image(systemName: "mic") }
                Button { showComposer = true } label: { Image(systemName: "text.bubble") }
                    .accessibilityIdentifier("composerButton")
                Menu {
                    Button { model.sendBytes("\u{03}") } label: { Label("Ctrl-C", systemImage: "xmark.octagon") }
                    Button { model.sendBytes("\u{1b}") } label: { Label("Esc", systemImage: "escape") }
                    Button { model.sendBytes("\u{0c}") } label: { Label("Ctrl-L 重繪", systemImage: "arrow.clockwise") }
                    Button { model.repaint() } label: { Label("重新載入畫面", systemImage: "arrow.triangle.2.circlepath") }
                    Divider()
                    Text(model.transportLabel.isEmpty ? "—" : model.transportLabel).font(.caption)
                } label: { Image(systemName: "ellipsis.circle") }
            }
        }
        .sheet(isPresented: $showComposer) {
            ComposerSheet(peerName: peer.name, sessionLabel: session?.label ?? ref.sid) { text, submit in
                try await model.conn?.send(sid: ref.sid, text: text, submit: submit)
            }
        }
        .sheet(isPresented: $showVoice) {
            VoiceSheet(target: WatchTarget(peerId: ref.peerId, peerName: peer.name, sid: ref.sid,
                                           label: session?.label ?? ref.sid, isAI: true)) { audio, name in
                try await model.conn?.voice(sid: ref.sid, audio: audio, filename: name) ?? ""
            }
        }
        .onAppear {
            model.fitMode = QAHooks.fitOverride ?? TerminalModel.defaultFitMode
            model.conn = store.connection(for: ref.peerId)
            model.peerSize = (session?.cols ?? 0, session?.rows ?? 0)
            model.start()
            store.clearAttention(peerId: ref.peerId, sid: ref.sid)
        }
        .onDisappear { model.stop() }
        .onChange(of: session?.cols) { _, _ in model.peerSize = (session?.cols ?? 0, session?.rows ?? 0) }
        .onChange(of: session?.rows) { _, _ in model.peerSize = (session?.cols ?? 0, session?.rows ?? 0) }
    }
}

// MARK: - Model

@MainActor
final class TerminalModel: NSObject, ObservableObject, TerminalViewDelegate {
    let ref: SessionRef
    var conn: PeerConnection?
    @Published var status: String?
    @Published var transportLabel: String = ""
    /// true = size the remote PTY to this screen (like the desktop remote pane);
    /// false = view-faithful: render at the computer's own cols×rows, pan/zoom.
    @Published var fitMode: Bool = TerminalModel.defaultFitMode {
        didSet { host?.applyLayout(); if fitMode { pushSizeSoon() } else { restorePeerSize() } }
    }
    @Published var peerSize: (Int, Int) = (0, 0) {
        didSet { if !fitMode { host?.applyLayout() } }
    }

    weak var host: TerminalHostView?
    private var stream: SessionStream?
    private var resizeTask: Task<Void, Never>?
    private var lastPushed: (Int, Int) = (0, 0)
    private var originalPeerSize: (Int, Int)?

    static var defaultFitMode: Bool {
        // iPad with a keyboard is a real workstation; phones just watch.
        UIDevice.current.userInterfaceIdiom == .pad
    }

    init(ref: SessionRef) {
        self.ref = ref
        super.init()
    }

    func start() {
        guard let conn, stream == nil else { return }
        let s = SessionStream(conn: conn, sid: ref.sid)
        s.onData = { [weak self] d in self?.host?.terminal.feed(text: d) }
        s.onReset = { [weak self] in self?.host?.resetScreen() }
        s.onStatus = { [weak self] st in
            self?.status = st
            Task { [weak self] in self?.transportLabel = await conn.transportLabel }
        }
        stream = s
        s.start()
        if fitMode { pushSizeSoon() }
    }

    func stop() {
        stream?.stop()
        stream = nil
        resizeTask?.cancel()
        // Leaving the view (back, background, quit) must not leave the desktop
        // squashed to this device's size: hand the tab its own size back.
        if fitMode, originalPeerSize != nil { restorePeerSize() }
    }

    func repaint() { stream?.requestRepaint() }

    func sendBytes(_ s: String) {
        guard let conn else { return }
        Task { try? await conn.input(sid: ref.sid, data: s) }
    }

    func toggleKeyboard() {
        guard let tv = host?.terminal else { return }
        if tv.isFirstResponder { tv.resignFirstResponder() } else { tv.becomeFirstResponder() }
    }

    /// Debounced `/link/resize` with the terminal's current geometry (fit mode only).
    func pushSizeSoon() {
        guard fitMode, let tv = host?.terminal else { return }
        let cols = tv.getTerminal().cols, rows = tv.getTerminal().rows
        guard cols > 4, rows > 2, (cols, rows) != lastPushed else { return }
        if originalPeerSize == nil, peerSize.0 > 0 { originalPeerSize = peerSize }
        resizeTask?.cancel()
        resizeTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 250_000_000)
            guard let self, !Task.isCancelled, let conn = self.conn else { return }
            do {
                try await conn.resize(sid: self.ref.sid, cols: cols, rows: rows)
                self.lastPushed = (cols, rows)
                self.stream?.requestRepaint()
            } catch {
                self.status = error.localizedDescription
            }
        }
    }

    /// Leaving fit mode: give the computer its own size back so the desktop isn't left squashed.
    private func restorePeerSize() {
        guard let conn, let orig = originalPeerSize, orig.0 > 0 else { return }
        lastPushed = (0, 0)
        Task {
            try? await conn.resize(sid: ref.sid, cols: orig.0, rows: orig.1)
            peerSize = orig
            stream?.requestRepaint()
        }
    }

    // MARK: TerminalViewDelegate

    func sizeChanged(source: TerminalView, newCols: Int, newRows: Int) {
        if fitMode { pushSizeSoon() }
    }
    func setTerminalTitle(source: TerminalView, title: String) {}
    func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}
    func send(source: TerminalView, data: ArraySlice<UInt8>) {
        guard let conn else { return }
        let s = String(decoding: data, as: UTF8.self)
        Task { try? await conn.input(sid: ref.sid, data: s) }
    }
    func scrolled(source: TerminalView, position: Double) {}
    func requestOpenLink(source: TerminalView, link: String, params: [String: String]) {
        if let u = URL(string: link) { UIApplication.shared.open(u) }
    }
    func bell(source: TerminalView) {}
    func clipboardCopy(source: TerminalView, content: Data) {
        UIPasteboard.general.string = String(decoding: content, as: UTF8.self)
    }
    func clipboardRead(source: TerminalView) -> Data? { nil }
    func iTermContent(source: TerminalView, content: ArraySlice<UInt8>) {}
    func rangeChanged(source: TerminalView, startY: Int, endY: Int) {}
}

// MARK: - UIKit host (fit vs fixed-size + zoom)

struct TerminalHost: UIViewRepresentable {
    @ObservedObject var model: TerminalModel

    func makeUIView(context: Context) -> TerminalHostView {
        let v = TerminalHostView(model: model)
        model.host = v
        return v
    }

    func updateUIView(_ uiView: TerminalHostView, context: Context) {
        uiView.applyLayout()
    }
}

final class TerminalHostView: UIView, UIScrollViewDelegate {
    let terminal: TerminalView
    private let scroll = UIScrollView()
    private weak var model: TerminalModel?
    private var lastBounds: CGRect = .zero

    static let bg = UIColor(red: 0x1a / 255, green: 0x1b / 255, blue: 0x26 / 255, alpha: 1)

    init(model: TerminalModel) {
        self.model = model
        let size = UIDevice.current.userInterfaceIdiom == .pad ? 13.0 : 12.0
        terminal = TerminalView(frame: CGRect(x: 0, y: 0, width: 320, height: 240),
                                font: UIFont.monospacedSystemFont(ofSize: size, weight: .regular))
        super.init(frame: .zero)
        backgroundColor = Self.bg
        terminal.nativeBackgroundColor = Self.bg
        terminal.nativeForegroundColor = UIColor(red: 0xa9 / 255, green: 0xb1 / 255, blue: 0xd6 / 255, alpha: 1)
        terminal.caretColor = UIColor(red: 0xc0 / 255, green: 0xca / 255, blue: 0xf5 / 255, alpha: 1)
        terminal.installColors(TokyoNight.ansi)
        terminal.terminalDelegate = model
        terminal.backgroundColor = Self.bg
        scroll.delegate = self
        scroll.backgroundColor = Self.bg
        scroll.bouncesZoom = true
        scroll.showsVerticalScrollIndicator = false
        scroll.contentInsetAdjustmentBehavior = .never
        addSubview(scroll)
        scroll.addSubview(terminal)
    }

    required init?(coder: NSCoder) { fatalError() }

    override func layoutSubviews() {
        super.layoutSubviews()
        scroll.frame = bounds
        if bounds != lastBounds {
            lastBounds = bounds
            applyLayout()
        }
    }

    func resetScreen() {
        // Full clear + home: what the desktop does with `term.reset()` before a repaint.
        terminal.feed(text: "\u{1b}[2J\u{1b}[3J\u{1b}[H")
    }

    func applyLayout() {
        guard bounds.width > 10, bounds.height > 10, let model else { return }
        if model.fitMode {
            scroll.minimumZoomScale = 1; scroll.maximumZoomScale = 1
            scroll.zoomScale = 1
            scroll.isScrollEnabled = false
            terminal.frame = CGRect(origin: .zero, size: bounds.size)
            scroll.contentSize = bounds.size
            terminal.setNeedsLayout()
        } else {
            let (c, r) = model.peerSize
            let cols = c > 0 ? c : 80, rows = r > 0 ? r : 24
            // Ask SwiftTerm for the exact pixel box of cols×rows, then make the view that
            // size (+ half a cell so floor() can't lose a column) and let the user pan/zoom.
            terminal.resize(cols: cols, rows: rows)
            var opt = terminal.getOptimalFrameSize()
            let cellW = opt.width / CGFloat(max(cols, 1)), cellH = opt.height / CGFloat(max(rows, 1))
            opt.size.width += cellW * 0.5
            opt.size.height += cellH * 0.5
            terminal.frame = CGRect(origin: .zero, size: opt.size)
            scroll.isScrollEnabled = true
            scroll.contentSize = opt.size
            let fitW = bounds.width / max(opt.width, 1)
            scroll.minimumZoomScale = min(1, fitW)
            scroll.maximumZoomScale = 3
            if scroll.zoomScale < scroll.minimumZoomScale || scroll.zoomScale == 1 && fitW < 1 {
                scroll.zoomScale = scroll.minimumZoomScale
            }
            terminal.setNeedsLayout()
        }
    }

    func viewForZooming(in scrollView: UIScrollView) -> UIView? { terminal }
}

enum TokyoNight {
    private static func c(_ hex: UInt32) -> SwiftTerm.Color {
        SwiftTerm.Color(red8: UInt16((hex >> 16) & 0xff), green8: UInt16((hex >> 8) & 0xff), blue8: UInt16(hex & 0xff))
    }
    /// Same 16 colours as the desktop xterm theme in web/index.html.
    static let ansi: [SwiftTerm.Color] = [
        c(0x15161e), c(0xf7768e), c(0x9ece6a), c(0xe0af68), c(0x7aa2f7), c(0xbb9af7), c(0x7dcfff), c(0xa9b1d6),
        c(0x414868), c(0xf7768e), c(0x9ece6a), c(0xe0af68), c(0x7aa2f7), c(0xbb9af7), c(0x7dcfff), c(0xc0caf5),
    ]
}

// MARK: - Composer (bridge-quality injection, like a Telegram message)

struct ComposerSheet: View {
    @Environment(\.dismiss) private var dismiss
    var peerName: String
    var sessionLabel: String
    var send: (String, Bool) async throws -> Void
    @State private var text = ""
    @State private var submit = true
    @State private var working = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 10) {
                Text("送到 \(peerName) › \(sessionLabel)。走與 Telegram 相同的注入路徑（等 AI 空檔、整段貼上），比逐字敲鍵盤穩。")
                    .font(.footnote).foregroundStyle(.secondary)
                TextEditor(text: $text)
                    .accessibilityIdentifier("composerText")
                    .font(.body.monospaced())
                    .frame(minHeight: 160)
                    .overlay(RoundedRectangle(cornerRadius: 8).stroke(.quaternary))
                Toggle("貼上後按 Enter 送出", isOn: $submit)
                if let e = error { Text(e).foregroundStyle(.red).font(.footnote) }
                Spacer()
            }
            .padding()
            .navigationTitle("輸入訊息")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button(working ? "送出中…" : "送出") {
                        working = true
                        Task {
                            do { try await send(text, submit); dismiss() }
                            catch { self.error = error.localizedDescription }
                            working = false
                        }
                    }
                    .disabled(working || text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityIdentifier("composerSend")
                }
            }
        }
        .presentationDetents([.medium, .large])
    }
}
