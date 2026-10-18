#!/usr/bin/env python3
"""SSH 執行器：正式環境唯讀、寫入要授權、不重啟服務、匯入時不連線。"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import a2a_ssh as S

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name + (f" | {detail}" if detail else ""))


def test_policy_and_argv():
    site = S.normalize("site", {
        "target": "site-box", "user": "deploy", "shell": "powershell",
        "production": True, "mode": "agent", "model": "claude-opus"})
    check("production cannot default to agent mode", site["mode"] == "plan")
    check("a third-party model is replaced", site["model"] == "composer-2.5")
    mode, err = S.gate(site, "看設定")
    check("production default is plan", mode == "plan" and err == "")
    mode, err = S.gate(site, "systemctl restart httpd", allow_write=True)
    check("a service restart is refused even with write permission",
          mode == "" and "restart" in err)
    mode, err = S.gate(site, "改一行", allow_write=True)
    built = S.argv(site, "改一行", mode)
    tokens = built[-1].split()
    check("an authorized write is agent mode without force",
          mode == "agent" and built[0] == "ssh" and "--force" not in tokens
          and "--mode" not in tokens and "Do not restart" in built[-1])
    check("the prompt is a single quoted argument",
          built[-1].rstrip().endswith("改一行'") and built[-2] == "deploy@site-box")
    nasty = S.argv(site, "'; reboot; '", "plan")
    check("a metacharacter stays inside the quotes",
          "'; reboot; '" not in nasty[-1] and "''; reboot; '''" in nasty[-1]
          or "''; reboot; ''" in nasty[-1])
    # PowerShell doubles single quotes: '...''; reboot; ''...'
    check("powershell quoting wraps the whole prompt",
          nasty[-1].count("reboot") == 1 and nasty[-1].split()[-1].startswith("'"))
    bad, err = S.gate(S.normalize("x", {"target": "box;rm"}), "看")
    check("a target with a shell metacharacter is refused", bad == "" and "target" in err)
    spark = S.normalize("spark", {"target": "spark-box"})
    built = S.argv(spark, "做", "agent")
    tokens = built[-1].split()
    check("a non-production worker may use force",
          "--force" in tokens and "--mode" not in tokens)
    calls = []
    ran = S.execute(site, "只看", runner=lambda argv: calls.append(argv) or {"code": 0, "out": "ok"})
    check("execute uses the injected runner", ran["ok"] and ran["out"] == "ok" and calls)
    check("execute without a runner does not connect",
          S.execute(site, "只看")["message"] == "no ssh runner")


def test_windows_prompt_safety():
    site = S.normalize("site", {"target": "site-box", "shell": "powershell",
                                "workspace": "C:\\proj dir\\"})
    task = 'echo "hi" %PATH% & dir\nsecond line'
    last = S.argv(site, task, "agent")[-1]
    check("powershell prompt has no double quote, percent or newline",
          '"' not in last and "%" not in last and "\n" not in last and "\r" not in last,
          last)
    check("the ampersand stays inside the single-quoted prompt",
          last.endswith("'echo ''hi'' ％PATH％ & dir second line'"), last)
    check("powershell workspace loses its trailing backslash",
          "--workspace 'C:\\proj dir'" in last, last)
    bare = S.argv(site, "status", "agent")[-1]
    check("a no-whitespace powershell task gets a trailing space",
          bare.endswith("'status '"), bare)
    slash = S.argv(site, "dir C:\\x\\", "agent")[-1]
    check("a trailing backslash is padded away from the closing quote",
          slash.endswith("\\ '"), slash)
    posix = S.normalize("spark", {"target": "spark-box", "shell": "posix"})
    kept = S.argv(posix, "line one\nline two", "agent")[-1]
    check("a posix worker keeps the newline as before",
          kept.endswith("'line one\nline two'") and "％" not in kept, kept)
    prod = S.normalize("site", {"target": "site-box", "shell": "powershell",
                                "production": True})
    note = S.argv(prod, "改一行", "agent")[-1]
    check("the write note stays one line on powershell", "\n" not in note, note)


def test_default_runner_failures():
    real = subprocess.run

    def boom(exc):
        def fake(*a, **k):
            raise exc
        return fake
    try:
        subprocess.run = boom(subprocess.TimeoutExpired("ssh", 5))
        slow = S.default_runner(["ssh", "site-box", "x"], timeout=5)
        subprocess.run = boom(FileNotFoundError("ssh"))
        missing = S.default_runner(["ssh", "site-box", "x"])
    finally:
        subprocess.run = real
    check("a hung ssh becomes code 124", slow["code"] == 124 and "timed out" in slow["err"])
    check("a missing ssh becomes code 127", missing["code"] == 127 and "not found" in missing["err"])


def test_import_does_not_spawn():
    calls = []
    real = subprocess.run
    subprocess.run = lambda *a, **k: calls.append(a)
    try:
        S.gate(S.normalize("spark", {"target": "spark-box"}), "看")
        S.argv(S.normalize("spark", {"target": "spark-box"}), "看", "plan")
    finally:
        subprocess.run = real
    check("building an argv does not spawn ssh", calls == [])


if __name__ == "__main__":
    test_policy_and_argv()
    test_windows_prompt_safety()
    test_default_runner_failures()
    test_import_does_not_spawn()
    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        for item in FAILED:
            print(" -", item)
        sys.exit(1)
    print("PASS")
