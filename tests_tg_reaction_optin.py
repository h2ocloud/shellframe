#!/usr/bin/env python3
"""表情回執預設關閉。

使用者的訊息是他自己的聊天記錄，不該被機器人加註記號。回執當初是為了補
「注入完到回覆之間的靜默」，但那個空窗現在由送達警告負責，所以回執改成
預設關、要的人再開（settings.tg_reactions）。

清除回執（emoji=None）不受開關影響，否則關掉之後舊的記號會永遠留在那裡。

跑法：.venv/bin/python tests_tg_reaction_optin.py
"""

import pathlib
import re
import sys

FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def main():
    here = pathlib.Path(__file__).parent
    bt = (here / "bridge_telegram.py").read_text(encoding="utf-8")
    mp = (here / "main.py").read_text(encoding="utf-8")

    check("default is off", '"tg_reactions": False' in mp)
    check("a gate helper exists", "def _reactions_enabled" in bt)
    check("the gate reads the setting",
          re.search(r"_reactions_enabled.*?tg_reactions", bt, re.S) is not None)

    # The async path is the one every call site uses, so gating it there is what
    # actually stops receipts appearing.
    m = re.search(r"def _react_async\(.*?\n(.*?)\n    def ", bt, re.S)
    body = m.group(1) if m else ""
    check("_react_async is gated", "_reactions_enabled" in body)
    check("clearing a reaction is still allowed",
          "emoji is not None" in body)

    # The receipt on the user's own inbound message must be behind the gate.
    m2 = re.search(r"origin_msg_id = msg\.get\(\"message_id\"\)(.{0,400})", bt, re.S)
    seg = m2.group(1) if m2 else ""
    check("inbound receipt is behind the gate", "_reactions_enabled" in seg)

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        sys.exit(1)
    print("PASS")


if __name__ == "__main__":
    main()
