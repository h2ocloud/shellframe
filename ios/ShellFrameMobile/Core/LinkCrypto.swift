import Foundation
import CryptoKit
import Security

/// Byte-for-byte port of the primitives in ShellFrame's `frame_link.py`.
/// Every string here must match the Python side exactly or the HMAC fails.
enum LinkCrypto {
    static func hexString(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }

    static func dataFromHex(_ hex: String) -> Data? {
        let chars = Array(hex.utf8)
        guard chars.count % 2 == 0 else { return nil }
        var out = Data(capacity: chars.count / 2)
        var i = 0
        while i < chars.count {
            guard let hi = hexVal(chars[i]), let lo = hexVal(chars[i + 1]) else { return nil }
            out.append(UInt8(hi << 4 | lo))
            i += 2
        }
        return out
    }

    private static func hexVal(_ c: UInt8) -> UInt8? {
        switch c {
        case 48...57: return c - 48
        case 97...102: return c - 87
        case 65...70: return c - 55
        default: return nil
        }
    }

    static func sha256Hex(_ data: Data) -> String {
        hexString(Data(SHA256.hash(data: data)))
    }

    static func hmacHex(key: Data, message: Data) -> String {
        let mac = HMAC<SHA256>.authenticationCode(for: message, using: SymmetricKey(data: key))
        return hexString(Data(mac))
    }

    static func randomHex(bytes: Int = 16) -> String {
        var d = Data(count: bytes)
        let rc = d.withUnsafeMutableBytes { buf -> Int32 in
            SecRandomCopyBytes(kSecRandomDefault, bytes, buf.baseAddress!)
        }
        if rc != errSecSuccess {
            // Fall back to the system RNG; SecRandomCopyBytes practically never fails.
            d = Data((0..<bytes).map { _ in UInt8.random(in: 0...255) })
        }
        return hexString(d)
    }

    /// `normalize_code`: drop separators, upper-case, keep the 32-char alphabet range.
    static func normalizeCode(_ code: String) -> String {
        String(code.uppercased().unicodeScalars.filter {
            ("A"..."Z").contains($0) || ("2"..."9").contains($0)
        }.map { Character($0) })
    }

    /// `_proof(code_norm, role, joiner_nonce, host_nonce, extra)`.
    static func pairProof(code: String, role: String, joinerNonce: String,
                          hostNonce: String, extra: String = "") -> String {
        let msg = "sf-pair-\(role)|\(joinerNonce)|\(hostNonce)|\(extra)"
        return hmacHex(key: Data(code.utf8), message: Data(msg.utf8))
    }

    /// `derive_secret(code_norm, joiner_nonce, host_nonce, joiner_id, host_id)`.
    static func deriveSecret(code: String, joinerNonce: String, hostNonce: String,
                             joinerId: String, hostId: String) -> String {
        let transcript = "sf-link-secret|\(joinerNonce)|\(hostNonce)|\(joinerId)|\(hostId)"
        return hmacHex(key: Data(code.utf8), message: Data(transcript.utf8))
    }

    /// `_string_to_sign(method, path_qs, ts, nonce, body_hash)` joined with "\n".
    static func signRequest(secretHex: String, method: String, pathQS: String,
                            ts: String, nonce: String, body: Data) -> String? {
        guard let key = dataFromHex(secretHex) else { return nil }
        let s = [method.uppercased(), pathQS, ts, nonce, sha256Hex(body)].joined(separator: "\n")
        return hmacHex(key: key, message: Data(s.utf8))
    }

    /// `_sign_response(peer, nonce, body)` = HMAC(secret, "resp\n{nonce}\n{sha256(body)}").
    static func expectedResponseSignature(secretHex: String, nonce: String, body: Data) -> String? {
        guard let key = dataFromHex(secretHex) else { return nil }
        return hmacHex(key: key, message: Data("resp\n\(nonce)\n\(sha256Hex(body))".utf8))
    }

    /// Python `str(time.time())` — any decimal float string parses on the other side.
    static func timestampString() -> String {
        String(format: "%.6f", Date().timeIntervalSince1970)
    }

    static func constantTimeEqual(_ a: String, _ b: String) -> Bool {
        let x = Array(a.utf8), y = Array(b.utf8)
        guard x.count == y.count else { return false }
        var diff: UInt8 = 0
        for i in 0..<x.count { diff |= x[i] ^ y[i] }
        return diff == 0
    }
}
