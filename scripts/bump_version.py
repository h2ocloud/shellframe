#!/usr/bin/env python3
"""原子挑選「下一個版號」——取 本機 + origin/main + git tags 的最大 semver，
patch +1（或 --minor / --major），寫回 version.json 並印出。

為什麼要這支：多個並行 session／機器同時開發同一 repo 時，各自手動把
version.json 改成「下一版」很容易撞號（都挑同一個號）。撞號雖然在
v0.29.24 起已不影響「其他機器偵測 update」（改用 git SHA 比對），但版號
本身還是不該重複。commit 前跑這支，就會拿到「比所有已知版號都大」的號。

用法：
    python3 scripts/bump_version.py            # patch +1
    python3 scripts/bump_version.py --minor    # minor +1、patch 歸零
    python3 scripts/bump_version.py --major
仍撞號時（極短併發窗）：push 被拒 → 再跑一次本支 → rebase → push。
"""
import json
import pathlib
import re
import subprocess
import sys

APP_DIR = pathlib.Path(__file__).resolve().parent.parent
VF = APP_DIR / "version.json"


def _vtuple(s):
    out = []
    for x in str(s).split("."):
        m = re.match(r"\d+", x)
        out.append(int(m.group()) if m else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out[:3])


def _git(*args):
    try:
        r = subprocess.run(["git", "-C", str(APP_DIR), *args],
                           capture_output=True, text=True, timeout=20)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def _versions():
    vs = []
    # 本機
    try:
        vs.append(_vtuple(json.loads(VF.read_text())["version"]))
    except Exception:
        pass
    # 遠端 main（先 fetch，best-effort）
    _git("fetch", "origin", "-q")
    show = _git("show", "origin/main:version.json")
    if show:
        try:
            vs.append(_vtuple(json.loads(show)["version"]))
        except Exception:
            pass
    # tags（vX.Y.Z）
    for ln in _git("tag").splitlines():
        m = re.match(r"v?(\d+\.\d+\.\d+)", ln.strip())
        if m:
            vs.append(_vtuple(m.group(1)))
    return vs or [(0, 0, 0)]


def main():
    bump = "patch"
    if "--minor" in sys.argv:
        bump = "minor"
    elif "--major" in sys.argv:
        bump = "major"
    hi = max(_versions())
    if bump == "major":
        nxt = (hi[0] + 1, 0, 0)
    elif bump == "minor":
        nxt = (hi[0], hi[1] + 1, 0)
    else:
        nxt = (hi[0], hi[1], hi[2] + 1)
    ver = ".".join(map(str, nxt))
    ch = "main"
    try:
        ch = json.loads(VF.read_text()).get("channel", "main")
    except Exception:
        pass
    VF.write_text(json.dumps({"version": ver, "channel": ch}, indent=2) + "\n")
    print(ver)


if __name__ == "__main__":
    main()
