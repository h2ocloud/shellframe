#!/usr/bin/env python3
"""run_tests.sh fails when a test changed the real Claude settings or wrote demo data into the office file.

11-19 a test wrote its own folder's hook path into the developer's real ~/.claude/settings.json and two
account profiles, and the run was green. scripts/user_files_guard.py takes a fingerprint before the run
and compares after. Here its idea of the real home is a temporary folder:
  - ShellBot's hook entries changing is caught, in ~/.claude and in a profile
  - other keys changing (Claude Code saving a permission) is not
  - demo data appearing in the office preferences is caught; the person's own changes are not
Run: python3 tests_user_files_guard.py
"""
import importlib.util
import json
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("user_files_guard", HERE / "scripts" / "user_files_guard.py")
G = importlib.util.module_from_spec(spec)
spec.loader.exec_module(G)

TMP = Path(tempfile.mkdtemp(prefix="sf-guard-"))
G.real_home = lambda: TMP
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + ("" if ok else f"  {detail}"))
    passed, failed = passed + bool(ok), failed + (not ok)


def hooks(cmd):
    return {"hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": cmd}]}],
                      "PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "say hi"}]}]}}


claude = TMP / ".claude" / "settings.json"
prof = TMP / ".config" / "shellframe" / "account-profiles" / "claude" / "p1" / "settings.json"
office = TMP / ".local" / "state" / "shellframe" / "office.json"
for p in (claude, prof, office):
    p.parent.mkdir(parents=True, exist_ok=True)
APP = 'python3 "/app/sf_agent_hook.py"'
claude.write_text(json.dumps(hooks(APP)), encoding="utf-8")
prof.write_text(json.dumps(hooks(APP)), encoding="utf-8")
office.write_text(json.dumps({"mode": "iso"}), encoding="utf-8")
snap = TMP / "snap.json"


def run():
    return G.main(["g", "check", str(snap)])


G.main(["g", "snapshot", str(snap)])
check("nothing changed: passes", run() == 0)

d = json.loads(claude.read_text()); d["permissions"] = {"allow": ["Bash(ls)"]}; d["hooks"]["PreToolUse"][0]["hooks"][0]["command"] = "say bye"
claude.write_text(json.dumps(d), encoding="utf-8")
check("other keys and other hooks changing: passes", run() == 0)

office.write_text(json.dumps({"mode": "flat", "seats": {"a": 1}}), encoding="utf-8")
check("the person's own office changes: passes", run() == 0)

d["hooks"]["Stop"][0]["hooks"][0]["command"] = 'python3 "/dev/folder/sf_agent_hook.py"'
claude.write_text(json.dumps(d), encoding="utf-8")
check("ShellBot's hook path changing in ~/.claude: fails", run() == 1)
claude.write_text(json.dumps(hooks(APP)), encoding="utf-8")

p = json.loads(prof.read_text()); p["hooks"]["SessionStart"] = [{"matcher": "", "hooks": [{"type": "command", "command": APP}]}]
prof.write_text(json.dumps(p), encoding="utf-8")
check("a hook event added in a profile: fails", run() == 1)
prof.write_text(json.dumps(hooks(APP)), encoding="utf-8")

office.write_text(json.dumps({"groups": [{"name": "示範工程組"}]}, ensure_ascii=False), encoding="utf-8")
check("demo data written into the office preferences: fails", run() == 1)
office.write_text("{}", encoding="utf-8")
check("back as it was: passes", run() == 0)

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else "FAILED")
raise SystemExit(1 if failed else 0)
