#!/usr/bin/env python3
"""cursor-agent 分頁：認指令、pin／resume、store.db、忙碌列、付費標籤、不估百分比。"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_cursor as C
import agent_status as A
import usage_probe as U
from _testsrc import app_source

FAILED = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name + (f" | {detail}" if detail else ""))


def test_detect_and_pin():
    check("cursor-agent is cursor", C.is_cursor_cmd("cursor-agent --trust"))
    check("windows launcher is cursor", C.is_cursor_cmd(r"C:\Users\a\cursor-agent.cmd"))
    check("sf-cursor launcher is cursor", C.is_cursor_cmd("sf-cursor --model composer-2.5"))
    check("agent is not cursor", not C.is_cursor_cmd("agent"))
    check("IDE cursor is not cursor-agent", not C.is_cursor_cmd("cursor ."))
    check("detect_ai ignores agent", U.detect_ai("agent") is None)
    check("detect_ai knows cursor-agent", U.detect_ai("/usr/local/bin/cursor-agent --model x") == "cursor")
    check("model flag does not reclassify as claude",
          A._worker_kind("cursor-agent --model claude-opus") == "cursor")
    check("wrapped claude is still claude", A._worker_kind("bash -lc claude") == "claude")

    pinned, sid = C.pin_session_id("cursor-agent --model composer-2.5 --trust --force")
    check("pin inserts the id immediately after the executable",
          pinned.startswith("cursor-agent --new-session-id ") and sid in pinned
          and "--model composer-2.5" in pinned)
    check("login is not pinned", C.pin_session_id("cursor-agent login")[1] is None)
    check("resume is not pinned again",
          C.pin_session_id(f"cursor-agent --resume {sid}")[1] is None)
    resumed = C.cmd_with_resume(pinned, "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    check("resume replaces the pinned id and keeps the model",
          "--resume aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in resumed
          and "--new-session-id" not in resumed and "composer-2.5" in resumed)


def test_store_transcript_status_and_billing():
    tmp = tempfile.mkdtemp(prefix="sf-cursor-")
    cfg = os.path.join(tmp, "config")
    data = os.path.join(tmp, "data")
    cwd = os.path.join(tmp, "work")
    os.makedirs(cwd)
    sid = str(uuid.uuid4())
    C._ROOT_OVERRIDE = (cfg, data)
    C._DIR_CACHE.clear()
    try:
        store = C._store_path(cwd, sid)
        os.makedirs(os.path.dirname(store))
        raw = json.dumps({
            "agentId": sid, "lastUsedModel": "composer-2.5[effort=high]",
            "createdAt": 1, "mode": "agent", "name": "demo",
            "blobEncryptionKey": "do-not-read",
        })
        con = sqlite3.connect(store)
        con.execute("CREATE TABLE meta (key TEXT, value TEXT)")
        con.execute("INSERT INTO meta VALUES (?, ?)", ("0", raw.encode().hex()))
        con.commit()
        con.close()
        worker = {"cmd": f"cursor-agent --new-session-id {sid}", "cwd": cwd, "tmux_name": ""}
        info = C.model_info(worker)
        check("store.db model wins and the key stays out",
              info == {"name": "composer-2.5", "effort": "high", "provider": "cursor",
                       "billing": "subscription"} and "do-not-read" not in str(info),
              str(info))
        check("billing classes",
              C.billing_class("auto") == "auto"
              and C.billing_class("claude-sonnet") == "paid"
              and C.billing_class("gpt-5") == "paid"
              and C.billing_class("gemini-2.5") == "paid"
              and C.billing_class("grok-4.7") == "subscription"
              and C.billing_class("mystery") == "unknown")

        path = os.path.join(data, "projects", C._slug(cwd), "agent-transcripts", sid, sid + ".jsonl")
        os.makedirs(os.path.dirname(path))
        row = {"role": "assistant", "message": {"content": [
            {"type": "text", "text": "hello"},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
        ]}}
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
            f.write(json.dumps({"type": "turn_ended"}) + "\n")
        check("transcript is found", C.transcript_path(worker) == path)
        fmt, evs, err = A._read_tail_events(path)
        kinds = [e.get("kind") for e in evs]
        check("cursor transcript is not claude",
              fmt == "cursor" and err is None and "assistant_text" in kinds
              and "tool_call" in kinds and "turn_end" in kinds, str(kinds))
        os.utime(path, (time.time(), time.time()))
        state, why = C.status(worker, now=time.time(), screen_tail="idle prompt")
        check("a fresh turn_ended is done", state == "done" and "turn ended" in why, why)
        os.utime(path, (time.time() - 60, time.time() - 60))
        state, why = C.status(worker, now=time.time(), screen_tail="idle prompt")
        check("an old turn_ended falls through to idle", state == "idle", why)
        state, why = C.status(worker, now=time.time(), screen_tail="ctrl+c to stop")
        check("busy footer is working", state == "working", why)
        state, why = C.status({"cmd": "cursor-agent", "cwd": cwd, "tmux_name": ""},
                              screen_tail="Press Ctrl+C again to exit")
        check("exit confirm is not busy", state != "working", why)

        quota = C.quota()
        check("quota is no_data and has no percentage",
              quota.get("_error") == "no_data" and "pct" not in quota
              and "week" not in quota)
        report = C.usage_report(worker)
        check("usage text names the model and not a guessed percent",
              report.startswith("AI 水位 Cursor Agent") and "composer-2.5" in report
              and "訂閱" in report and "%" not in report, report)
        calls = []
        real = subprocess.run
        subprocess.run = lambda *a, **k: calls.append(a)
        try:
            C.quota()
            U.probe_data("cursor-agent")
        finally:
            subprocess.run = real
        shaped = U.probe_data("cursor-agent")
        check("probe does not invent a percent or spawn",
              calls == [] and shaped.get("week") is None and shaped.get("five_hr") is None
              and shaped.get("error") == "no_data", str(shaped))
    finally:
        C._ROOT_OVERRIDE = None
        C._DIR_CACHE.clear()


def test_wiring():
    src = app_source()
    check("preset pins a subscription model",
          "Cursor Agent" in src and "composer-2.5" in src and "--trust --force" in src)
    check("main pins and resumes cursor without treating it as claude",
          "agent_cursor.pin_session_id" in src and "agent_cursor.cmd_with_resume" in src
          and 'entry["cursor_session_id"]' in src)
    check("registry has cursor", "cursor" in U.PROVIDER_SPECS
          and U.PROVIDER_SPECS["cursor"]["binaries"] == ("cursor-agent",))


if __name__ == "__main__":
    test_detect_and_pin()
    test_store_transcript_status_and_billing()
    test_wiring()
    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        for item in FAILED:
            print(" -", item)
        sys.exit(1)
    print("PASS")
