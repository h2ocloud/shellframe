import SwiftUI
import AVFoundation

struct RecorderView: View {
    @EnvironmentObject var link: PhoneLink
    @StateObject private var rec = WatchRecorder()
    @State private var error: String?
    @State private var showPicker = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 10) {
                    Button { showPicker = true } label: {
                        VStack(spacing: 2) {
                            Text(link.selected?.label ?? "選擇分頁").font(.headline).lineLimit(1)
                            Text(link.selected?.peerName ?? (link.targets.isEmpty ? "手機上還沒有配對／分頁" : "")).font(.caption2).foregroundStyle(.secondary).lineLimit(1)
                        }
                    }
                    .buttonStyle(.bordered)
                    .disabled(link.targets.isEmpty)

                    Button { toggle() } label: {
                        ZStack {
                            Circle().fill(rec.isRecording ? Color.red : Color.accentColor).frame(width: 78, height: 78)
                            Image(systemName: rec.isRecording ? "stop.fill" : "mic.fill").font(.system(size: 30)).foregroundStyle(.white)
                        }
                    }
                    .buttonStyle(.plain)
                    .disabled(link.selected == nil)

                    Text(rec.isRecording ? "錄音中 \(rec.seconds)s · 再按送出" : (link.pending.isEmpty ? "按一下開始錄音" : "轉寫中…"))
                        .font(.footnote).foregroundStyle(.secondary)
                    if let e = error { Text(e).font(.caption2).foregroundStyle(.red) }

                    ForEach(link.results.prefix(3)) { r in
                        HStack(alignment: .top, spacing: 6) {
                            Image(systemName: r.ok ? "checkmark.circle.fill" : "xmark.circle.fill")
                                .foregroundStyle(r.ok ? .green : .red)
                            Text(r.text.isEmpty ? "（沒有文字）" : r.text).font(.caption2).lineLimit(4)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(6)
                        .background(.quaternary.opacity(0.3), in: RoundedRectangle(cornerRadius: 8))
                    }
                }
                .padding(.horizontal, 4)
            }
            .navigationTitle("ShellFrame")
            .sheet(isPresented: $showPicker) {
                List(link.targets) { t in
                    Button {
                        link.select(t); showPicker = false
                    } label: {
                        VStack(alignment: .leading) {
                            Text(t.label).lineLimit(1)
                            Text(t.peerName).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            .onAppear { link.refreshTargets() }
        }
    }

    private func toggle() {
        error = nil
        if rec.isRecording {
            guard let url = rec.stop() else { error = "錄音太短"; return }
            guard let t = link.selected else { return }
            _ = link.sendVoice(fileURL: url, to: t)
        } else {
            Task {
                guard await rec.requestPermission() else { error = "沒有麥克風權限"; return }
                do { try rec.start() } catch { self.error = error.localizedDescription }
            }
        }
    }
}

/// AVAudioRecorder on the watch: AAC m4a, 16 kHz mono.
@MainActor
final class WatchRecorder: NSObject, ObservableObject {
    @Published var isRecording = false
    @Published var seconds = 0
    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private var url: URL?

    func requestPermission() async -> Bool {
        await AVAudioApplication.requestRecordPermission()
    }

    func start() throws {
        let s = AVAudioSession.sharedInstance()
        try s.setCategory(.record, mode: .default, options: [])
        try s.setActive(true)
        let u = FileManager.default.temporaryDirectory.appendingPathComponent("w-\(Int(Date().timeIntervalSince1970)).m4a")
        let r = try AVAudioRecorder(url: u, settings: [
            AVFormatIDKey: kAudioFormatMPEG4AAC,
            AVSampleRateKey: 16000.0,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue,
        ])
        r.record()
        recorder = r; url = u
        isRecording = true; seconds = 0
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.seconds += 1 }
        }
    }

    func stop() -> URL? {
        timer?.invalidate(); timer = nil
        recorder?.stop(); recorder = nil
        isRecording = false
        try? AVAudioSession.sharedInstance().setActive(false)
        guard let u = url, let size = try? FileManager.default.attributesOfItem(atPath: u.path)[.size] as? Int, size > 1000 else { return nil }
        return u
    }
}
