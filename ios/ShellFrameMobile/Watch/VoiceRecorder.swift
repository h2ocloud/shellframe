import Foundation
import AVFoundation

/// Records a short voice note as AAC/m4a (16 kHz mono) — small enough to ship
/// through a relay, and ffmpeg on the Mac converts it for whisper.
@MainActor
final class VoiceRecorder: NSObject, ObservableObject, AVAudioRecorderDelegate {
    @Published var isRecording = false
    @Published var seconds: Int = 0
    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private(set) var fileURL: URL?

    static let settings: [String: Any] = [
        AVFormatIDKey: kAudioFormatMPEG4AAC,
        AVSampleRateKey: 16000.0,
        AVNumberOfChannelsKey: 1,
        AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue,
    ]

    func requestPermission() async -> Bool {
        if #available(iOS 17.0, watchOS 10.0, *) {
            return await AVAudioApplication.requestRecordPermission()
        } else {
            return await withCheckedContinuation { c in
                AVAudioSession.sharedInstance().requestRecordPermission { c.resume(returning: $0) }
            }
        }
    }

    func start() throws {
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .default, options: [.allowBluetooth, .defaultToSpeaker])
        try session.setActive(true)
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("voice-\(Int(Date().timeIntervalSince1970)).m4a")
        let r = try AVAudioRecorder(url: url, settings: Self.settings)
        r.delegate = self
        r.record()
        recorder = r
        fileURL = url
        isRecording = true
        seconds = 0
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.seconds += 1 }
        }
    }

    /// Stops and returns the recorded bytes (nil when nothing usable was captured).
    func stop() -> Data? {
        timer?.invalidate(); timer = nil
        recorder?.stop()
        recorder = nil
        isRecording = false
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        guard let url = fileURL, let data = try? Data(contentsOf: url), data.count > 1000 else { return nil }
        return data
    }

    func discard() {
        if let url = fileURL { try? FileManager.default.removeItem(at: url) }
        fileURL = nil
    }
}
