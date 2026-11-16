#!/usr/bin/env python3
"""Keys typed on another computer into a tab here (/link/input → raw_input): a repeated IME chunk is dropped.

Reported: typing Chinese into this computer's tab from another computer's sidebar doubled whole phrases
("但感覺還是要引導使用者去操作但感覺還是要引導使用者去操作gcp"). The debug log showed the same
14-character chunk written twice, 40 ms apart, each time just before an English word. The sending page is
fixed (tests_ime_seq.py M1–M5), but the other computer may run an older one, so this computer drops the
second copy too.
  - the same multi-character non-ASCII chunk for the same tab within 0.4 s: dropped
  - a different tab, a different chunk, or later than that: written
  - ASCII and single characters are never touched (a held key, "哈哈" typed as two commits)
  - raw_input checks before it writes
Run: .venv/bin/python tests_remote_input_repeat.py
"""
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.modules.setdefault("webview", MagicMock())
import api_remote  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


clock = [1000.0]
api_remote.time = types.SimpleNamespace(time=lambda: clock[0])
api = types.SimpleNamespace()
rep = lambda sid, data: api_remote._repeat_ime_chunk(api, sid, data)  # noqa: E731

PHRASE = "但感覺還是要引導使用者去操作"
check("the first copy is written", rep("s1", PHRASE) is False)
clock[0] += 0.04
check("the same phrase 40 ms later is dropped", rep("s1", PHRASE) is True)
clock[0] += 0.059
check("…and a third copy right after it too", rep("s1", PHRASE) is True)
clock[0] += 0.5
check("the same phrase half a second later is written (typed again)", rep("s1", PHRASE) is False)
clock[0] += 0.01
check("another tab is not affected", rep("s2", PHRASE) is False)
clock[0] += 0.01
check("a different chunk is written", rep("s1", "的感覺") is False)
clock[0] += 0.01
check("a single character twice is written (哈哈)", rep("s1", "哈") is False and rep("s1", "哈") is False)
check("ASCII twice is written (a held key, 'ee')", rep("s1", "ee") is False and rep("s1", "ee") is False)
check("a backspace-and-fix from the new page is written", rep("s1", "\x7f了") is False and rep("s1", "了") is False)

src = (HERE / "api_remote.py").read_text(encoding="utf-8")
branch = src.split('elif cmd == "raw_input":', 1)[1].split("elif cmd", 1)[0]
check("raw_input checks before it writes", branch.index("_repeat_ime_chunk(") < branch.index("s.write(data)"))

print(f"\n{passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
