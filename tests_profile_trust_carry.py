#!/usr/bin/env python3
"""切了帳號的分頁不該每次開新分頁都被問一次信任（v0.35.16）。

回報：每開一個新分頁都會碰到權限詢問。那不是 macOS 的權限，是 Claude Code 的
「這個目錄可信嗎」對話框。

根因：Claude Code 把信任紀錄寫在 CLAUDE_CONFIG_DIR 底下的 .claude.json。切了
帳號的分頁指到 account profile 目錄，而那份設定是空的——使用者的家目錄在標準
設定裡明明早就是 True，profile 裡卻停在 False（另一個 profile 連檔案都沒有），
所以每開一個新分頁都會再問一次。原本靠一個 watcher 送 Down/Enter 去回答，實測
會失敗（log：「trust dialog still up after keys」）——那是在跟 TUI 賽跑。

修法只搬「使用者已經做過的決定」：標準設定說信任的目錄才帶進 profile，沒說的
照樣跳對話框。決定權還是使用者的，只是不會因為換帳號被問第二次。

跑法：.venv/bin/python tests_profile_trust_carry.py
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
sys.modules['webview'] = MagicMock()
sys.modules['bridge_telegram'] = MagicMock()
sys.path.insert(0, str(HERE))

import main as _main  # noqa: E402

# 測試不得寫進 production 的 debug log——那份 log 是使用者除錯用的，測試的
# 假路徑混進去只會誤導。
_main._dlog = lambda *a, **k: None

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


class _FakeSession:
    _carry_trust_to_profile = _main.Session._carry_trust_to_profile

    def __init__(self, cmd, cwd, sid="s1"):
        self.cmd = cmd
        self.cwd = cwd
        self.sid = sid


def run(cmd, cwd, config_dir, canonical_blob, profile_blob=None):
    """跑真正的 _carry_trust_to_profile，回傳 profile 那份 .claude.json。"""
    td = tempfile.mkdtemp(prefix="sf-trust-")
    home = os.path.join(td, "home")
    os.makedirs(home)
    canonical = Path(home) / ".claude.json"
    if canonical_blob is not None:
        canonical.write_text(json.dumps(canonical_blob), encoding="utf-8")
    prof = os.path.join(td, "profile") if config_dir else ""
    if prof:
        os.makedirs(prof, exist_ok=True)
        if profile_blob is not None:
            (Path(prof) / ".claude.json").write_text(
                json.dumps(profile_blob), encoding="utf-8")
    orig = os.path.expanduser
    os.path.expanduser = lambda p: p.replace("~", home, 1) if p.startswith("~") else p
    try:
        _FakeSession(cmd, cwd)._carry_trust_to_profile(
            {"CLAUDE_CONFIG_DIR": prof} if prof else {})
        target = Path(prof) / ".claude.json" if prof else None
        if target and target.exists():
            return json.loads(target.read_text(encoding="utf-8"))
        return None
    finally:
        os.path.expanduser = orig
        import shutil
        shutil.rmtree(td, ignore_errors=True)


HOME_TRUSTED = {"projects": {"/Users/alice": {"hasTrustDialogAccepted": True,
                                              "allowedTools": ["Bash"]}}}
CLAUDE = "claude --permission-mode bypassPermissions"

# 1. profile 完全沒有 .claude.json（實測有這種）→ 建出來並帶入信任
blob = run(CLAUDE, "/Users/alice", True, HOME_TRUSTED, profile_blob=None)
check("profile 沒有設定檔時會建出來",
      blob is not None and "projects" in blob, str(blob))
check("信任被帶進 profile",
      (blob or {}).get("projects", {}).get("/Users/alice", {})
      .get("hasTrustDialogAccepted") is True, str(blob))

# 2. profile 的紀錄停在 False（實測就是這個狀態）→ 要被扶正
blob = run(CLAUDE, "/Users/alice", True, HOME_TRUSTED,
           profile_blob={"projects": {"/Users/alice":
                                      {"hasTrustDialogAccepted": False,
                                       "lastCost": 1.5}}})
check("profile 停在 False 會被帶成 True",
      blob["projects"]["/Users/alice"]["hasTrustDialogAccepted"] is True, str(blob))
check("profile 原有的其他欄位沒被清掉",
      blob["projects"]["/Users/alice"].get("lastCost") == 1.5, str(blob))

# 3. 只搬已經存在的決定——標準設定沒信任就不要代替使用者決定
blob = run(CLAUDE, "/Users/alice/secret", True, HOME_TRUSTED)
check("標準設定沒信任的目錄不會被自動信任",
      (blob or {}).get("projects", {}).get("/Users/alice/secret") is None, str(blob))
blob = run(CLAUDE, "/Users/alice", True,
           {"projects": {"/Users/alice": {"hasTrustDialogAccepted": False}}})
check("標準設定明確是 False 時也不搬",
      (blob or {}).get("projects", {}).get("/Users/alice") is None, str(blob))
blob = run(CLAUDE, "/Users/alice", True, None)
check("標準設定不存在時什麼都不做", blob is None, str(blob))

# 4. 只對 claude 分頁做；沒 pin 帳號的分頁不必動（它讀的就是標準設定）
blob = run("sf-codex --search", "/Users/alice", True, HOME_TRUSTED)
check("codex 分頁不碰", blob is None, str(blob))
blob = run("bash", "/Users/alice", True, HOME_TRUSTED)
check("bash 分頁不碰", blob is None, str(blob))
blob = run(CLAUDE, "/Users/alice", False, HOME_TRUSTED)
check("沒 pin 帳號的分頁不必動（它本來就讀標準設定）", blob is None, str(blob))

# 5. wrapper 也算 claude（跟其他地方同一支分類器）
blob = run("sf-claude-home", "/Users/alice", True, HOME_TRUSTED)
check("sf-claude-home 這種 wrapper 也認得",
      (blob or {}).get("projects", {}).get("/Users/alice", {})
      .get("hasTrustDialogAccepted") is True, str(blob))

# 6. profile 的設定檔壞掉不能讓開分頁失敗，也不能把好資料吃掉
td = tempfile.mkdtemp(prefix="sf-trust-bad-")
home = os.path.join(td, "home"); os.makedirs(home)
Path(home, ".claude.json").write_text(json.dumps(HOME_TRUSTED), encoding="utf-8")
prof = os.path.join(td, "profile"); os.makedirs(prof)
Path(prof, ".claude.json").write_text("{ this is not json", encoding="utf-8")
_orig = os.path.expanduser
os.path.expanduser = lambda p: p.replace("~", home, 1) if p.startswith("~") else p
try:
    _FakeSession(CLAUDE, "/Users/alice")._carry_trust_to_profile(
        {"CLAUDE_CONFIG_DIR": prof})
    out = json.loads(Path(prof, ".claude.json").read_text(encoding="utf-8"))
    check("profile 設定檔壞掉時不炸，重建成可用的內容",
          out["projects"]["/Users/alice"]["hasTrustDialogAccepted"] is True, str(out))
finally:
    os.path.expanduser = _orig
    import shutil
    shutil.rmtree(td, ignore_errors=True)

# 7. 三條 spawn 路徑都要帶（tmux / unix / windows）
main_src = (HERE / "main.py").read_text(encoding="utf-8")
check("三條 spawn 路徑都在 spawn 前帶信任",
      main_src.count("self._carry_trust_to_profile(") == 3,
      f"只有 {main_src.count('self._carry_trust_to_profile(')} 處")
carry = main_src.split("def _carry_trust_to_profile")[1].split("\n    def ")[0]
check("寫檔用原子替換（Claude Code 自己也在寫這個檔）",
      "_atomic_write_json(" in carry
      and "os.replace(" in main_src.split("def _atomic_write_json")[1]
                                   .split("\ndef ")[0],
      carry[-400:])


# ── 8. Fix 1：家目錄 + AI 分頁，標準設定缺這筆/停在 False → 主動扶正 ──
#    （不是只搬「已經是 True」的決定。這是 watcher 本來就會自動接受的範圍。）
def run_canonical(cmd, cwd_is_home, canonical_blob):
    """跑 _carry_trust_to_profile（沒 pin 帳號），回傳標準設定被改成什麼。"""
    td = tempfile.mkdtemp(prefix="sf-canon-")
    home = os.path.join(td, "home")
    os.makedirs(home)
    canonical = Path(home) / ".claude.json"
    if canonical_blob is not None:
        canonical.write_text(json.dumps(canonical_blob), encoding="utf-8")
    cwd = home if cwd_is_home else os.path.join(td, "elsewhere")
    orig_exp = os.path.expanduser
    os.path.expanduser = lambda p: p.replace("~", home, 1) if p.startswith("~") else p
    orig_trusted = _main.TRUSTED_STARTUP_CWDS
    _main.TRUSTED_STARTUP_CWDS = {home, str(Path(home).resolve())}
    try:
        _FakeSession(cmd, cwd)._carry_trust_to_profile({})
        return json.loads(canonical.read_text(encoding="utf-8")) if canonical.exists() else None, cwd
    finally:
        os.path.expanduser = orig_exp
        _main.TRUSTED_STARTUP_CWDS = orig_trusted
        import shutil
        shutil.rmtree(td, ignore_errors=True)


def _any_trusted(blob):
    return bool(blob) and any(
        v.get("hasTrustDialogAccepted") is True
        for v in (blob.get("projects") or {}).values())


blob, cwd = run_canonical(CLAUDE, True, {"projects": {}})
check("Fix1: 標準設定沒這筆 → 家目錄 AI 分頁自動補 True",
      _any_trusted(blob), str(blob))

blob2, cwd2 = run_canonical(CLAUDE, True, None)
check("Fix1: 標準設定完全不存在時,家目錄 AI 分頁也建得出來並補 True",
      _any_trusted(blob2), str(blob2))

blob, cwd = run_canonical(CLAUDE, False, {"projects": {}})
check("Fix1: 非家目錄仍然不代替使用者決定",
      (blob or {}).get("projects", {}).get(cwd) is None, str(blob))

blob, cwd = run_canonical("bash", True, {"projects": {}})
check("Fix1: 非 AI 分頁不碰標準設定",
      (blob or {}).get("projects", {}).get(cwd) is None, str(blob))


# ── 9. Fix 2：Windows 正斜線 key ── canonical 存 "C:/x"、cwd 傳 "C:\\x" 要對得上
def run_win(cwd, config_dir, canonical_blob, profile_blob=None):
    td = tempfile.mkdtemp(prefix="sf-win-")
    home = os.path.join(td, "home")
    os.makedirs(home)
    Path(home, ".claude.json").write_text(json.dumps(canonical_blob), encoding="utf-8")
    prof = os.path.join(td, "profile")
    os.makedirs(prof)
    if profile_blob is not None:
        Path(prof, ".claude.json").write_text(json.dumps(profile_blob), encoding="utf-8")
    orig_exp = os.path.expanduser
    os.path.expanduser = lambda p: p.replace("~", home, 1) if p.startswith("~") else p
    orig_win = _main.IS_WIN
    _main.IS_WIN = True
    try:
        _FakeSession(CLAUDE, cwd)._carry_trust_to_profile({"CLAUDE_CONFIG_DIR": prof})
        t = Path(prof, ".claude.json")
        return json.loads(t.read_text(encoding="utf-8")) if t.exists() else None
    finally:
        os.path.expanduser = orig_exp
        _main.IS_WIN = orig_win
        import shutil
        shutil.rmtree(td, ignore_errors=True)


out = run_win(r"C:\Users\bob", True,
              {"projects": {"C:/Users/bob": {"hasTrustDialogAccepted": True}}})
check("Fix2: canonical 正斜線 key 對得上 cwd 的反斜線",
      out is not None
      and any(v.get("hasTrustDialogAccepted") is True
              for v in out.get("projects", {}).values()),
      str(out))

out = run_win(r"C:\Users\bob", True,
              {"projects": {"C:/Users/bob": {"hasTrustDialogAccepted": True}}},
              profile_blob={"projects": {"C:/Users/bob": {"hasTrustDialogAccepted": False,
                                                          "lastCost": 2}}})
check("Fix2: 已有的正斜線 key 直接沿用,不會多長一個反斜線 key",
      out is not None and list(out.get("projects", {}).keys()) == ["C:/Users/bob"]
      and out["projects"]["C:/Users/bob"]["hasTrustDialogAccepted"] is True
      and out["projects"]["C:/Users/bob"].get("lastCost") == 2,
      str(out))


# ── 10. Fix 3：一次性提示旗標從標準設定鏡射進 profile ──
out = run(CLAUDE, "/Users/alice", True,
          {"projects": {"/Users/alice": {"hasTrustDialogAccepted": True}},
           "hasCompletedOnboarding": True,
           "fullscreenUpsellSeenCount": 3,
           "lastOnboardingVersion": "2.1.74"},
          profile_blob={"projects": {"/Users/alice": {"hasTrustDialogAccepted": True}}})
check("Fix3: onboarding 旗標鏡射進 profile",
      out.get("hasCompletedOnboarding") is True, str(out))
check("Fix3: fullscreen 升級提示計數鏡射進 profile（就是那個 renderer 提示）",
      out.get("fullscreenUpsellSeenCount") == 3, str(out))
check("Fix3: onboarding 版本字串鏡射進 profile",
      out.get("lastOnboardingVersion") == "2.1.74", str(out))

out = run(CLAUDE, "/Users/alice", True,
          {"projects": {"/Users/alice": {"hasTrustDialogAccepted": True}},
           "fullscreenUpsellSeenCount": 1},
          profile_blob={"projects": {"/Users/alice": {"hasTrustDialogAccepted": True}},
                        "fullscreenUpsellSeenCount": 5})
check("Fix3: profile 既有值較大時不倒退",
      out.get("fullscreenUpsellSeenCount") == 5, str(out))

check("Fix3: 標準設定沒帶某旗標時 profile 也不會被塞",
      "hasSeenTasksHint" not in out, str(out))


# ── 11. Fix 3：預寫建立起信任後,watcher 不再 arm ──
_seed_src = main_src.split("def _carry_trust_to_profile")[1].split("\n    def ")[0]
check("Fix3: 預寫成功建立信任 → 關掉 _startup_trust_pending（watcher 不 arm）",
      "self._startup_trust_pending = False" in _seed_src, _seed_src[-300:])


print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
