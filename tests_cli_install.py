#!/usr/bin/env python3
"""The install button never pastes a bash pipeline into a Windows PowerShell tab.

The provider registry holds macOS / Linux commands (`curl … | bash`). On Windows the
install tab is PowerShell, so a new user who pressed "install" for Claude Code got an
error. cli_install picks each CLI's own PowerShell installer, keeps npm and irm
commands, and offers no command (only the note and docs link) for the rest.

Run: .venv/bin/python tests_cli_install.py
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import cli_install  # noqa: E402
import usage_probe  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


SPECS = usage_probe.PROVIDER_SPECS
win = {p: cli_install.for_platform(p, s.get("install"), True) for p, s in SPECS.items()}
unix = {p: cli_install.for_platform(p, s.get("install"), False) for p, s in SPECS.items()}

print("Windows")
pipes = {p: w["command"] for p, w in win.items() if re.search(r"\|\s*(ba|z)?sh\b", w.get("command", ""))}
check("no CLI gets a bash pipeline on Windows", not pipes, str(pipes))
for p, url in (("claude", "https://claude.ai/install.ps1"),
               ("agy", "https://antigravity.google/cli/install.ps1"),
               ("grok", "https://x.ai/cli/install.ps1")):
    check(f"{p}: its own PowerShell installer", win[p].get("command") == f"irm {url} | iex", win[p].get("command"))
check("cursor: the Windows installer agent_cursor documents",
      win["cursor"].get("command") == "irm 'https://cursor.com/install?win32=true' | iex", win["cursor"].get("command"))
for p in ("codex", "pi"):
    check(f"{p}: npm stays, with a note that Node.js comes first",
          win[p].get("command", "").startswith("npm ") and "Node.js" in win[p].get("note", ""), str(win[p]))
check("opencode: no command, a note on what to do instead, and the docs link",
      "command" not in win["opencode"] and win["opencode"].get("note") and win["opencode"].get("docs"),
      str(win["opencode"]))
check("agy: the macOS / Linux note (~/.local/bin, shell profile) is not shown on Windows",
      "~/.local/bin" not in win["agy"].get("note", ""), win["agy"].get("note"))
check("grok: its login note stays", "grok login" in win["grok"].get("note", ""), win["grok"].get("note"))
check("every Windows command comes with the restart-from-the-shortcut hint",
      all(cli_install.AFTER_INSTALL in w.get("note", "") for w in win.values() if w.get("command")))
check("every CLI still has its docs link on Windows", all(w.get("docs") for w in win.values()))

print("macOS / Linux")
check("unchanged from the registry", all(unix[p] == (SPECS[p].get("install") or {}) for p in SPECS))
check("the registry itself is not modified", all("irm " not in (s.get("install") or {}).get("command", "")
                                                  or p == "cursor" for p, s in SPECS.items()))

print("wiring")
src = (Path(__file__).parent / "api_accounts.py").read_text(encoding="utf-8")
body = src[src.index("    def install_provider"):src.index("    def account_usage_all")]
check("the install button runs the platform's command", "self._install_info(provider)" in body)
check("the not-installed dialog, the provider list and the accounts panel show the same",
      src.count("self._install_info(") >= 4, str(src.count("self._install_info(")))
check("…and nothing reads the raw registry install dict any more",
      '.get("install") or {}' not in src.replace('spec.get("install"), os.name', ""))

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
