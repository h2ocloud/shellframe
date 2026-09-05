import SwiftUI

/// Record → send to the Mac's STT chain → injected into the tab. Shows the transcript back.
struct VoiceSheet: View {
    @Environment(\.dismiss) private var dismiss
    var target: WatchTarget
    var send: (Data, String) async throws -> String
    @StateObject private var rec = VoiceRecorder()
    @State private var phase: Phase = .idle
    @State private var transcript = ""
    @State private var error: String?

    enum Phase { case idle, recording, sending, done }

    var body: some View {
        NavigationStack {
            VStack(spacing: 18) {
                Text("\(target.peerName) › \(target.label)").font(.footnote).foregroundStyle(.secondary)
                Spacer()
                Button {
                    Task { await toggle() }
                } label: {
                    ZStack {
                        Circle().fill(phase == .recording ? Color.red : Color.accentColor).frame(width: 110, height: 110)
                        Image(systemName: phase == .recording ? "stop.fill" : "mic.fill")
                            .font(.system(size: 40)).foregroundStyle(.white)
                    }
                }
                .disabled(phase == .sending)
                Text(label).font(.headline)
                if phase == .recording { Text("\(rec.seconds) 秒").monospacedDigit().foregroundStyle(.secondary) }
                if phase == .done {
                    ScrollView {
                        Text(transcript.isEmpty ? "（沒有辨識出文字）" : transcript)
                            .font(.body).frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .frame(maxHeight: 160)
                    .padding(10)
                    .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
                }
                if let e = error { Text(e).foregroundStyle(.red).font(.footnote) }
                Spacer()
                Text("錄音會傳到電腦，用電腦上設定的 whisper 轉文字，再像 Telegram 語音一樣貼進這個分頁。")
                    .font(.caption2).foregroundStyle(.secondary).multilineTextAlignment(.center)
            }
            .padding()
            .navigationTitle("語音輸入")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("關閉") { if rec.isRecording { _ = rec.stop() }; rec.discard(); dismiss() } } }
        }
        .presentationDetents([.medium, .large])
    }

    private var label: String {
        switch phase {
        case .idle: return "按一下開始錄音"
        case .recording: return "錄音中…再按一下送出"
        case .sending: return "轉寫並送進分頁中…"
        case .done: return "已送出"
        }
    }

    private func toggle() async {
        error = nil
        switch phase {
        case .idle, .done:
            guard await rec.requestPermission() else { error = "沒有麥克風權限"; return }
            do { try rec.start(); phase = .recording } catch { self.error = error.localizedDescription }
        case .recording:
            guard let data = rec.stop() else { error = "錄音太短"; phase = .idle; return }
            phase = .sending
            do {
                transcript = try await send(data, "voice.m4a")
                phase = .done
            } catch {
                self.error = error.localizedDescription
                phase = .idle
            }
            rec.discard()
        case .sending: break
        }
    }
}
