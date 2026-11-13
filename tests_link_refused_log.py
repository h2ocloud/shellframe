#!/usr/bin/env python3
"""A refused Frame Link request leaves a line in the log (v0.44.5).

Reported: a tablet's messages never reached either computer, and neither computer had any
trace of it -- a request that failed verification was answered 403 and forgotten. Now:
  - a refused request is logged with the route, the claimed peer and the reason,
    at most once a minute per peer and reason (a phone retries every few seconds);
  - the signature, nonce and body never appear in the line;
  - an accepted POST (other than per-keystroke /link/input and /link/resize) is logged with
    its route, the peer and the target tab, not the text.

Run: .venv/bin/python tests_link_refused_log.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tests_frame_link as tfl  # noqa: E402

check, FAILED = tfl.check, tfl.FAILED


def main():
    a, b = tfl.Node("frameA"), tfl.Node("frameB")
    logs = []
    a.link._log = logs.append
    try:
        a.link.start()
        b.link.start()
        pair = a.link.pairing_begin()
        check("paired", b.link.join("127.0.0.1", a.port, pair["code"]).get("success") is True)
        peer = b.link.peers()[a.fid]
        logs.clear()

        sig = "0" * 64
        st, _ = tfl.signed_get(b, peer, "/link/info", sig=sig)
        refused = [m for m in logs if "refused" in m]
        check("a bad signature is refused (403)", st == 403)
        check("…and logged with the route, the peer and the reason",
              len(refused) == 1 and "GET /link/info" in refused[0] and b.fid[:8] in refused[0]
              and "bad signature" in refused[0] and "frameB" in refused[0])
        check("…without the signature", sig not in refused[0])

        for _ in range(5):
            tfl.signed_get(b, peer, "/link/info", sig=sig)
        check("the same peer and reason is logged once a minute, not per retry",
              len([m for m in logs if "refused" in m]) == 1)

        tfl.signed_get(b, peer, "/link/info", ts=str(time.time() - 3600))
        check("another reason gets its own line",
              len([m for m in logs if "refused" in m and "timestamp" in m]) == 1)

        st, _ = tfl.post_json(f"http://127.0.0.1:{a.port}/link/send", {"sid": "s1", "text": "hi"})
        check("an unsigned POST is refused and logged",
              st == 403 and any("refused POST /link/send from peer ?" in m for m in logs))

        logs.clear()
        secret_words = "這是不該寫進 log 的內容"
        st, body = tfl.signed_post(b, peer, "/link/send", {"sid": "s1", "text": secret_words, "submit": True})
        check("a signed send still goes through", st == 200 and body.get("success"))
        sent = [m for m in logs if "/link/send" in m]
        check("…and is logged with the peer and the tab", len(sent) == 1 and "frameB" in sent[0] and "s1" in sent[0])
        check("…but not the text", all(secret_words not in m for m in logs))

        logs.clear()
        tfl.signed_post(b, peer, "/link/input", {"sid": "s1", "data": "x"})
        check("per-keystroke /link/input is not logged", not [m for m in logs if "/link/input" in m])
    finally:
        a.cleanup()
        b.cleanup()
    print(f"\n{'FAILED ' + str(len(FAILED)) if FAILED else 'all passed'}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()
