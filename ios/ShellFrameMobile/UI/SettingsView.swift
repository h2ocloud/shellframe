import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var store: PeerStore
    @Environment(\.dismiss) private var dismiss
    @State private var name = DeviceIdentity.deviceName

    var body: some View {
        NavigationStack {
            Form {
                Section("這支裝置") {
                    TextField("裝置名稱（電腦側欄顯示用）", text: $name)
                        .onSubmit { DeviceIdentity.deviceName = name }
                    LabeledContent("frame_id", value: store.myId).font(.caption.monospaced())
                }
                Section("顯示") {
                    Text(UIDevice.current.userInterfaceIdiom == .pad
                         ? "iPad 預設「撐滿」：會把電腦上該分頁的終端改成 iPad 的尺寸，外接鍵盤直接操作。"
                         : "iPhone 預設「照電腦尺寸」：忠實顯示電腦畫面，可捏合縮放；要打字時可切成撐滿。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("安全") {
                    Text("Frame Link 是 HMAC 簽章、未加密的 HTTP。區網直連沒問題；走公網請用你自己的 relay（HTTPS），relay 主機看得到終端內容。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                Section("關於") {
                    LabeledContent("版本", value: Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "")
                    Text("ShellFrame Mobile 是電腦端 Frame Link 的手機 peer：配對一次，之後跟本機分頁一樣看、一樣打。")
                        .font(.footnote).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("設定")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { DeviceIdentity.deviceName = name; dismiss() } } }
        }
    }
}
