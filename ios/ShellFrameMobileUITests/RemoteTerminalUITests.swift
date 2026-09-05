import XCTest

/// Drives the real app against a real paired computer (the simulator must already be
/// paired). Types into the mirrored terminal and through the composer, then saves
/// screenshots to $SF_QA_OUT so the run leaves visual evidence behind.
///
/// Env (pass with TEST_RUNNER_ prefix to xcodebuild): SF_QA_SID (tab to open, default
/// first live one), SF_QA_FIT (1/0), SF_QA_OUT (directory for PNGs), SF_QA_TAG (text tag).
final class RemoteTerminalUITests: XCTestCase {
    private var env: [String: String] { ProcessInfo.processInfo.environment }
    private var tag: String { env["SF_QA_TAG"] ?? (UIDevice.current.userInterfaceIdiom == .pad ? "ipad" : "iphone") }

    private func launch() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["SF_QA_AUTOSELECT"] = env["SF_QA_SID"] ?? "1"
        app.launchEnvironment["SF_QA_FIT"] = env["SF_QA_FIT"] ?? "1"
        app.launch()
        return app
    }

    private func save(_ app: XCUIApplication, _ name: String) {
        let shot = XCUIScreen.main.screenshot()
        let att = XCTAttachment(screenshot: shot)
        att.name = name
        att.lifetime = .keepAlways
        add(att)
        if let dir = env["SF_QA_OUT"] {
            try? shot.pngRepresentation.write(to: URL(fileURLWithPath: dir).appendingPathComponent(name))
        }
    }

    func testKeyboardReachesRemotePTY() throws {
        let app = launch()
        let term = app.otherElements["terminal"].firstMatch
        XCTAssertTrue(term.waitForExistence(timeout: 30), "terminal never appeared — is the simulator paired?")
        sleep(4)                                   // snapshot + stream attach + (fit) resize settle
        app.buttons["keyboardButton"].firstMatch.tap()
        sleep(1)
        app.typeText("echo hello-from-\(tag)-keyboard $((6*7))\n")
        sleep(4)                                   // round trip: PTY echo → stream → screen
        save(app, "10_\(tag)_typed.png")
    }

    func testComposerSendsThroughBridgePath() throws {
        let app = launch()
        let term = app.otherElements["terminal"].firstMatch
        XCTAssertTrue(term.waitForExistence(timeout: 30))
        sleep(3)
        app.buttons["composerButton"].firstMatch.tap()
        let editor = app.textViews["composerText"].firstMatch
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        editor.tap()
        app.typeText("echo hello-from-\(tag)-composer")
        save(app, "11_\(tag)_composer.png")
        app.buttons["composerSend"].firstMatch.tap()
        sleep(5)
        save(app, "12_\(tag)_composer_result.png")
    }
}
