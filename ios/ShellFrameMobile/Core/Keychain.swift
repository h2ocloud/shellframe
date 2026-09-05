import Foundation
import Security
import os

let sfLog = Logger(subsystem: "com.howard.shellframe", category: "link")

/// Per-peer HMAC secrets: Keychain first; if the Keychain is unavailable (e.g. an
/// unsigned simulator build returns -34018 errSecMissingEntitlement) fall back to a
/// file in the app container with complete file protection, so pairing never
/// silently loses its key.
enum Keychain {
    private static let service = "com.howard.shellframe.peer-secret"

    static func set(_ value: String, account: String) {
        if !keychainSet(value, account: account) { FileSecrets.set(value, account: account) }
    }

    static func get(account: String) -> String? {
        keychainGet(account: account) ?? FileSecrets.get(account: account)
    }

    static func delete(account: String) {
        keychainDelete(account: account)
        FileSecrets.delete(account: account)
    }

    @discardableResult
    private static func keychainSet(_ value: String, account: String) -> Bool {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attrs: [String: Any] = [
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        let status = SecItemUpdate(query as CFDictionary, attrs as CFDictionary)
        if status == errSecItemNotFound {
            var add = query
            add.merge(attrs) { $1 }
            let st = SecItemAdd(add as CFDictionary, nil)
            if st != errSecSuccess { sfLog.error("keychain add failed: \(st)"); return false }
            return true
        } else if status != errSecSuccess {
            sfLog.error("keychain update failed: \(status)")
            return false
        }
        return true
    }

    private static func keychainGet(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let st = SecItemCopyMatching(query as CFDictionary, &item)
        guard st == errSecSuccess, let data = item as? Data else {
            if st != errSecItemNotFound { sfLog.error("keychain get failed: \(st)") }
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    private static func keychainDelete(account: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
    }
}


/// Fallback secret store (app container, `.completeFileProtection`). Used only when
/// the Keychain refuses (missing entitlement); never preferred over it.
enum FileSecrets {
    private static var dir: URL {
        let d = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ShellFrame/secrets", isDirectory: true)
        try? FileManager.default.createDirectory(at: d, withIntermediateDirectories: true,
                                                 attributes: [.protectionKey: FileProtectionType.complete])
        return d
    }

    private static func url(_ account: String) -> URL {
        dir.appendingPathComponent(account.replacingOccurrences(of: "/", with: "_") + ".key")
    }

    static func set(_ value: String, account: String) {
        try? Data(value.utf8).write(to: url(account), options: [.atomic, .completeFileProtection])
    }

    static func get(account: String) -> String? {
        (try? Data(contentsOf: url(account))).flatMap { String(data: $0, encoding: .utf8) }
    }

    static func delete(account: String) {
        try? FileManager.default.removeItem(at: url(account))
    }
}
