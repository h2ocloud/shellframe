import Foundation

/// Pulls a remote tab's raw PTY output (`/link/stream`) and drives a terminal view.
/// Mirrors the desktop web UI: paint the current screen first, then attach the
/// incremental cursor and poll — fast while output is flowing, backing off when idle.
@MainActor
final class SessionStream {
    let conn: PeerConnection
    let sid: String
    var onData: (String) -> Void = { _ in }
    var onReset: () -> Void = {}
    var onStatus: (String?) -> Void = { _ in }

    private var task: Task<Void, Never>?
    private var since = -1
    private var wantRepaint = true
    private(set) var running = false

    init(conn: PeerConnection, sid: String) {
        self.conn = conn
        self.sid = sid
    }

    func start() {
        guard task == nil else { return }
        running = true
        task = Task { [weak self] in await self?.run() }
    }

    func stop() {
        running = false
        task?.cancel()
        task = nil
    }

    /// After a resize the peer repaints everything; drop our cursor and redraw from a snapshot.
    func requestRepaint() {
        wantRepaint = true
    }

    private func repaint() async {
        do {
            if let ansi = try await conn.snapshot(sid: sid) {
                onReset()
                onData(ansi)
            } else {
                // Older peer without /link/snapshot: plain-text peek like the web UI does.
                let text = try await conn.peek(sid: sid, lines: 200)
                onReset()
                onData(text.replacingOccurrences(of: "\n", with: "\r\n"))
            }
        } catch {
            // Non-fatal: the stream will fill the screen as output arrives.
        }
        // (Re)attach: the server returns its current cursor and no data.
        if let ch = try? await conn.stream(sid: sid, since: -1) { since = ch.seq }
        wantRepaint = false
    }

    private func run() async {
        var idle = 0
        while !Task.isCancelled {
            if wantRepaint { await repaint() }
            do {
                let ch = try await conn.stream(sid: sid, since: since)
                if Task.isCancelled { break }
                if ch.reset && since >= 0 {
                    // Ring buffer overran our cursor → full redraw.
                    since = ch.seq
                    await repaint()
                    continue
                }
                if !ch.data.isEmpty {
                    onData(ch.data)
                    idle = 0
                } else {
                    idle += 1
                }
                since = ch.seq
                onStatus(nil)
            } catch LinkError.cancelled {
                break
            } catch {
                if Task.isCancelled { break }
                onStatus(error.localizedDescription)
                idle = 8
            }
            let viaRelay = await conn.transportLabel.hasPrefix("relay")
            let floorMs: UInt64 = viaRelay ? 150 : 40
            let ms: UInt64 = idle == 0 ? floorMs : min(500, floorMs + UInt64(idle) * 60)
            try? await Task.sleep(nanoseconds: ms * 1_000_000)
        }
    }
}
