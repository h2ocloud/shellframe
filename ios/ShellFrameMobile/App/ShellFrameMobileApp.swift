import SwiftUI

@main
struct ShellFrameMobileApp: App {
    @StateObject private var store = PeerStore()
    @StateObject private var watch = WatchBridge()
    @Environment(\.scenePhase) private var scenePhase

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .environmentObject(watch)
                .onAppear {
                    watch.attach(store: store)
                    store.startAutoRefresh()
                }
                .onChange(of: scenePhase) { _, phase in
                    switch phase {
                    case .active: store.startAutoRefresh()
                    case .background: store.stopAutoRefresh()
                    default: break
                    }
                }
        }
    }
}
