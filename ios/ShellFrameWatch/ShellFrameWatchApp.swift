import SwiftUI

@main
struct ShellFrameWatchApp: App {
    @StateObject private var link = PhoneLink()

    var body: some Scene {
        WindowGroup {
            RecorderView()
                .environmentObject(link)
        }
    }
}
