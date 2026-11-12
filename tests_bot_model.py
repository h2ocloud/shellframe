#!/usr/bin/env python3
"""A bot's model, end to end on the Python side (docs/bots.md "Updating the model list",
docs/mobile-link.md `choices.models`): the lists every CLI offers, what a pick does to the
launch command, a wrong key that is refused, and a wrong key that is already stored.

Asked for: the model is chosen from a list built from ShellBot's own table, because a typed key
that was not exactly the CLI's was stored and silently did nothing.
  - /link/bots says, for every CLI it lists, what its picker offers (the same list the page gets)
  - choosing a model puts exactly that key on the command the bot wakes with, for every CLI
    that can pin one; "CLI default" leaves none
  - a key ShellBot does not know is refused by every way in (the editor's save, sfctl bots-set,
    a phone's /link/bots/settings) and nothing is written; the refusal says "不認得這個模型"
    and offers the likely key
  - a key stored before is flagged (the page's roster `pin`, /link/bots `model_ok`), not refused again
    when only something else is edited, and the wake says so in the log and to the person
Demo roster and tabs. Run: .venv/bin/python tests_bot_model.py
"""
import json
import os
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).resolve().parent
TMP = Path(tempfile.mkdtemp(prefix="sf-bot-model-"))
os.environ["SHELLFRAME_BOTS_DIR"] = str(TMP / "bots")
os.environ["SHELLFRAME_PROMPT_LANG"] = "zh"
sys.modules["webview"] = MagicMock()
sys.path.insert(0, str(HERE))
import sf_log  # noqa: E402
sf_log.DEBUG_LOG = str(TMP / "debug.log")
import agent_model  # noqa: E402
import api_bots  # noqa: E402
import main  # noqa: E402
from main import Api  # noqa: E402

main.CONFIG_FILE = TMP / "config.json"
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


CMD = {"claude": "claude --dangerously-skip-permissions", "codex": "sf-codex --search", "opencode": "opencode",
       "grok": "grok --no-alt-screen", "cursor": "cursor-agent --trust --force", "pi": "pi", "agy": "agy"}
FLAG = {"claude": "--model", "codex": "-m", "opencode": "--model", "grok": "-m", "cursor": "--model"}
LABELS = {"claude": "Claude Code", "codex": "Codex", "pi": "Pi", "opencode": "OpenCode", "agy": "Antigravity",
          "grok": "Grok Build", "cursor": "Cursor Agent"}
api_bots._CLIS.update(at=time.time() + 3600, list=[
    {"key": k, "label": LABELS[k], "installed": True, "models": k in agent_model.PROVIDERS} for k in LABELS])


class Tab:
    def __init__(self, sid, cmd, label=""):
        self.sid, self.cmd, self._custom_label, self.alive = sid, cmd, label, True
        self._tmux_name, self._recent, self.lock = None, bytearray(), threading.Lock()
        self._startup_trust_pending, self._init_pending = True, True
        self._last_output_activity_time = 0.0


def config(roster=None):
    main.CONFIG_FILE.write_text(json.dumps({"settings": {"experimental_bots": True},
                                            "agent_roster": roster or {}}, ensure_ascii=False), encoding="utf-8")


def stored():
    return json.loads(main.CONFIG_FILE.read_text(encoding="utf-8")).get("agent_roster", {})


def model_of(role):
    return stored()[role].get("model", "")


api = Api.__new__(Api)
api.sessions = {}
api.bridge = api.line_bridge = None
api._window = None
api.launched = []                                              # (sid, command the tab was started with)
counter = [100]


def new_session(cmd, cols, rows, source="manual", handoff=False, inherit_accounts=True):
    counter[0] += 1
    sid = f"s{counter[0]}"
    api.sessions[sid] = Tab(sid, cmd)
    api.launched.append((sid, cmd))
    return sid


api.new_session = new_session
api.rename_session = lambda sid, name, manual=False: (setattr(api.sessions[sid], "_custom_label", name), '{"success": true}')[1]
api._send_text_to_session = lambda s, text, submit=False: True
api._wait_until_ready = lambda sid, timeout=45.0: sid in api.sessions
api._agent_state_for_list = lambda sid: ""
api._agent_status_snapshot = lambda sid: ({}, 0)
api._agent_blocked_for_list = lambda sid: ""
api._last_activity_at = lambda sid, s, out_ts=0.0: 0.0
api._ordered_sids = lambda cfg=None, preferred_order=None: list(api.sessions)
api._bots_notify_ui = lambda: None
api.preset_groups = lambda: json.dumps([{"provider": k, "items": [{"cmd": CMD[k]}]} for k in CMD])
logged = []
api_bots._dlog = lambda tag, msg: logged.append((tag, msg))


def save(role, **data):
    return json.loads(api.bots_save(role, json.dumps(data, ensure_ascii=False), ""))


def link():
    return api._execute_sfctl("bots_link", {})


print("1. /link/bots: what each CLI's picker offers")
config({"寫手": {"label": "寫手", "cmd": "claude", "bot": {"memory": True}}})
choices = link()["choices"]
clis = [c["key"] for c in choices["clis"]]
check("a list for every CLI the phone can switch to", sorted(choices["models"]) == sorted(clis) and len(clis) == 7, sorted(choices["models"]))
for cli in clis:
    got = choices["models"][cli]
    pins = {c["key"]: c["models"] for c in choices["clis"]}[cli]
    check(f"{cli}: the table's list, 'CLI 預設' first (key \"\"), " + ("Other last and flagged custom" if pins else "and only that: grey the field"),
          got == agent_model.choices(cli, zh=True) and got[0] == {"key": "", "label": "CLI 預設"}
          and (got[-1].get("custom") is True and got[-1]["key"] == agent_model.CUSTOM if pins else len(got) == 1), got)
check("the listed models are the table's, by name and key (Claude Code)",
      [(c["key"], c["label"]) for c in choices["models"]["claude"][1:-1]]
      == [("opus", "Opus"), ("sonnet", "Sonnet"), ("haiku", "Haiku"), ("fable", "Fable")])
check("a CLI that takes a model on its command line says so in clis, and its list agrees",
      all((c["models"] is True) == (len(choices["models"][c["key"]]) > 1) for c in choices["clis"]))
info = json.loads(api.bots_model_info(json.dumps(["claude --model haiku --x", "pi", "my-wrapper --y", CMD["codex"]])))
check("…bots_model_info: provider, pinnable, the flag and the pin already on the command, why a command takes none",
      info["by_cmd"]["claude --model haiku --x"] == {"provider": "claude", "pinnable": True, "pinned": "haiku", "flag": "--model", "reason": ""}
      and info["by_cmd"]["pi"] == {"provider": "", "pinnable": False, "pinned": "", "flag": "", "reason": "fixed"}
      and info["by_cmd"]["my-wrapper --y"]["reason"] == "unknown" and info["by_cmd"][CMD["codex"]]["provider"] == "codex", info["by_cmd"])
check("…and the list per provider is the table's", info["choices"]["claude"][1:-1] == choices["models"]["claude"][1:-1]
      and [c["key"] for c in info["choices"][""]] == [""], info["choices"].keys())
check("a bad request is an empty answer, not an error", json.loads(api.bots_model_info("nope")) == {"choices": {}, "by_cmd": {}})

print("2. choosing a model puts it on the launch command")
for cli in FLAG:
    for key in [c["key"] for c in agent_model.choices(cli)[1:-1]]:
        role = f"{cli}-{key}"
        config()
        r = save(role, cmd=CMD[cli], model=key, memory=True)
        api.launched.clear()
        opened = json.loads(api.bots_open(role))
        cmd = api.launched[-1][1] if api.launched else ""
        check(f"{cli} {key}: saved, and the bot wakes with {FLAG[cli]} {key} on its command",
              r.get("success") and opened.get("success") and cmd == agent_model.apply(CMD[cli], key)
              and cmd.count(FLAG[cli] + " ") == 1 and cmd.endswith(f"{FLAG[cli]} {key}")
              and stored()[role]["model"] == key, (r, cmd))
config()
save("預設", cmd=CMD["claude"], model="", memory=True)
api.launched.clear()
api.bots_open("預設")
check("CLI default: nothing is pinned on the command", api.launched[-1][1] == CMD["claude"] and stored()["預設"]["model"] == "", api.launched)
config()
save("省", cmd=CMD["claude"] + " --model haiku", memory=True)
check("a command that already pins a model, and no model given: kept as it is",
      stored()["省"]["cmd"] == CMD["claude"] + " --model haiku" and model_of("省") == "", stored()["省"])
save("省", model="")
check("choosing 'CLI default' takes that pin off the command too (it must really be the default)",
      stored()["省"]["cmd"] == CMD["claude"] and stored()["省"]["model"] == "", stored()["省"])
save("省", model="sonnet")
api.launched.clear()
api.bots_open("省")
check("a model chosen over a pinned command replaces it, once", api.launched[-1][1] == CMD["claude"] + " --model sonnet", api.launched)
r = save("省", cmd=CMD["codex"])
check("another CLI with no model given: the last CLI's model is dropped (sonnet would not fit codex)",
      r.get("success") and stored()["省"]["model"] == "" and stored()["省"]["cmd"] == CMD["codex"], stored()["省"])

print("3. a key ShellBot does not know is refused, everywhere, and nothing is written")
config({"寫手": {"label": "寫手", "cmd": CMD["claude"], "model": "sonnet", "responsibility": "寫文件", "bot": {"memory": True}},
        "查": {"label": "查", "cmd": CMD["pi"], "bot": {"memory": True}}})
before = json.dumps(stored(), sort_keys=True, ensure_ascii=False)
for what, key, code in (("a typo", "sonet", "unknown_model"), ("another CLI's model", "gpt-5", "unknown_model"),
                        ("the wrong case", "Sonnet", "unknown_model"), ("two words", "Sonnet 4.5", "bad_model"),
                        ("a shell word", "opus;id", "bad_model")):
    r = save("寫手", model=key)
    check(f"the editor's save: {what} ({key!r}) is refused with {code}", r.get("success") is False and r.get("code") == code, r)
    r = api._bots_switch("寫手", "", key)
    check(f"sfctl bots-set / a switch: {what} is refused with {code}", r.get("success") is False and r.get("code") == code, r)
    r = api._execute_sfctl("bots_settings", {"role": "寫手", "model": key})
    check(f"a phone's /link/bots/settings: {what} is refused", r.get("success") is False
          and (r.get("code") == code or "一個詞" in r.get("message", "")), r)
check("none of them wrote anything", json.dumps(stored(), sort_keys=True, ensure_ascii=False) == before)
r = save("寫手", model="sonet")
check("the refusal says what is wrong, in words, and which key was likely meant",
      "不認得這個模型" in r["message"] and "sonet" in r["message"] and "opus、sonnet、haiku、fable" in r["message"]
      and r["suggestion"] == "sonnet" and "是不是要「sonnet」" in r["message"], r)
r = save("寫手", model="gpt-5.5")
check("a model of another CLI is not accepted on this one", r.get("code") == "unknown_model")
r = save("查", model="x")
check("a CLI that picks its model inside it takes none", r.get("code") == "model_fixed" and "要在它裡面選" in r["message"], r)
r = api._bots_switch("寫手", "codex", "sonnet")
check("…and the model is checked against the CLI the bot is moving to (sonnet is no Codex model)",
      r.get("code") == "unknown_model" and stored()["寫手"]["cmd"] == CMD["claude"], r)
r = api._bots_switch("寫手", "codex", "gpt-5-codex")
check("…a Codex model is fine there", r.get("success") and stored()["寫手"]["model"] == "gpt-5-codex"
      and agent_model.provider_of(stored()["寫手"]["cmd"]) == "codex", (r, stored()["寫手"]))
r = api._bots_switch("寫手", "claude")
check("a switch that names no model drops the last CLI's, and keeps the new command's own pin",
      r.get("success") and stored()["寫手"]["model"] == "", stored()["寫手"])
for good in ("opus", "claude-opus-4-8", "sonnet[1m]", "opusplan", "fable"):
    check(f"a key the CLI takes is accepted: {good}", save("寫手", model=good).get("success") and stored()["寫手"]["model"] == good)
r = json.loads(api.bots_model_check(CMD["claude"], "sonet"))
check("the editor's live check: not ok, the code, the likely key, the words",
      r["ok"] is False and r["code"] == "unknown_model" and r["suggestion"] == "sonnet" and "不認得這個模型" in r["message"], r)
r = json.loads(api.bots_model_check(CMD["claude"], "claude-haiku-4-5-20251001"))
check("…and for one that is fine", r["ok"] is True and r["kind"] == "known" and r["message"] == "", r)
r = json.loads(api.bots_model_check("my-wrapper", "x"))
check("…a command ShellBot does not know takes none, and says so", r["code"] == "model_fixed" and "沒辦法在這個指令上指定模型" in r["message"], r)

print("4. a wrong key that is already stored")
config({"壞的": {"label": "壞的", "cmd": CMD["claude"], "model": "sonet", "responsibility": "舊的", "bot": {"memory": True}},
        "大寫": {"label": "大寫", "cmd": "claude --model Opus", "bot": {"memory": True}},
        "固定": {"label": "固定", "cmd": "pi", "model": "gpt-5", "bot": {"memory": True}},
        "好的": {"label": "好的", "cmd": CMD["codex"], "model": "gpt-5-codex", "bot": {"memory": True}},
        "指令裡": {"label": "指令裡", "cmd": "claude --model haiku", "bot": {"memory": True}},
        "預設": {"label": "預設", "cmd": "claude", "bot": {"memory": True}},
        "外部": {"label": "外部", "cmd": "zsh", "model": "whatever",
               "bot": {"external": {"kind": "webhook", "url": "https://example.com/h"}}}})
roster = json.loads(api.bots_state())["roster"]
pin = lambda role: roster[role]["pin"]
check("the page's roster says it: a typo is not ok, and which key was meant",
      pin("壞的")["ok"] is False and pin("壞的")["code"] == "unknown_model" and pin("壞的")["suggestion"] == "sonnet"
      and pin("壞的")["model"] == "sonet", pin("壞的"))
check("…one only in the command (--model Opus) is flagged too", pin("大寫")["ok"] is False and pin("大寫")["model"] == "Opus", pin("大寫"))
check("…a model kept on a CLI that takes none does nothing, and is flagged",
      pin("固定")["ok"] is False and pin("固定")["code"] == "model_fixed", pin("固定"))
check("…good ones are not: listed, in the command, or none",
      pin("好的")["ok"] and pin("好的")["kind"] == "listed" and pin("指令裡")["ok"] and pin("指令裡")["model"] == "haiku"
      and pin("預設")["ok"] and pin("預設")["kind"] == "default", [pin(r) for r in ("好的", "指令裡", "預設")])
phone = {b["role"]: b for b in link()["bots"]}
check("the phone's /link/bots: model_ok false, why, and the likely key",
      phone["壞的"]["model_ok"] is False and phone["壞的"]["model_code"] == "unknown_model"
      and phone["壞的"]["model_suggestion"] == "sonnet" and phone["固定"]["model_code"] == "model_fixed", phone["壞的"])
check("the page's roster also names each role's CLI and the model as a person reads it",
      roster["壞的"]["cli"] == {"key": "claude", "label": "Claude Code"} and roster["固定"]["cli"] == {"key": "pi", "label": "Pi"}
      and roster["好的"]["pin"]["label"] == "GPT-5 Codex" and roster["指令裡"]["pin"]["label"] == "Haiku"
      and roster["預設"]["pin"]["label"] == "", (roster["壞的"]["cli"], roster["好的"]["pin"]))
check("…true for the rest, with nothing to say; an outside bot runs where it lives, so it is ok",
      all(phone[r]["model_ok"] is True and phone[r]["model_code"] == "" for r in ("好的", "指令裡", "預設", "外部")))
r = save("壞的", responsibility="新的執掌", model="sonet")
check("editing only the job, with the stored key sent back unchanged: saved, the key is kept and still flagged",
      r.get("success") and stored()["壞的"]["responsibility"] == "新的執掌" and stored()["壞的"]["model"] == "sonet"
      and json.loads(api.bots_state())["roster"]["壞的"]["pin"]["ok"] is False, r)
r = save("大寫", responsibility="只在指令裡的舊 key", model="Opus")
check("a wrong key that is only in the command (--model Opus) counts as stored too: editing the job is not blocked by it",
      r.get("success") and stored()["大寫"]["responsibility"] == "只在指令裡的舊 key", r)
r = save("壞的", model="sonet2")
check("…but a different wrong key is refused", r.get("code") == "unknown_model" and stored()["壞的"]["model"] == "sonet", r)
r = save("壞的", cmd=CMD["codex"], model="sonet")
check("…and the same key on another CLI is not 'unchanged': refused", r.get("code") == "unknown_model"
      and agent_model.provider_of(stored()["壞的"]["cmd"]) == "claude", r)
r = save("固定", model="")
check("a model on a CLI that takes none is cleared by saving the editor's empty one",
      r.get("success") and stored()["固定"]["model"] == "" and json.loads(api.bots_state())["roster"]["固定"]["pin"]["ok"], r)
r = save("壞的", model="sonnet")
check("choosing a good one clears the flag", r.get("success") and json.loads(api.bots_state())["roster"]["壞的"]["pin"]["ok"]
      and {b["role"]: b for b in link()["bots"]}["壞的"]["model_ok"] is True)
config({"壞的": {"label": "壞的", "cmd": CMD["claude"], "model": "sonet", "bot": {"memory": True}}})
logged.clear()
api.launched.clear()
r = json.loads(api.bots_open("壞的"))
check("waking it: the tab opens, the person is told, and the log says it",
      r["success"] and "不認得這個模型" in r["details"].get("model_warning", "")
      and any(t == "bots" and "sonet" in m and "unknown_model" in m for t, m in logged), (r, logged[-2:]))
check("…and the command is what the table gives, not something quietly different",
      api.launched[-1][1] == agent_model.apply(CMD["claude"], "sonet"), api.launched)
config({"好的": {"label": "好的", "cmd": CMD["codex"], "model": "gpt-5-codex", "bot": {"memory": True}}})
r = json.loads(api.bots_open("好的"))
check("a good model: no warning", r["success"] and "model_warning" not in r["details"], r)

config({"數字": {"label": "數字", "cmd": CMD["claude"], "model": 5, "bot": {"memory": True}},
        "空": {"label": "空", "cmd": CMD["claude"], "model": None, "bot": {"memory": True}}})
check("a model that is not text (a hand-edited config) is read as text: flagged, and nothing breaks for the others",
      json.loads(api.bots_state())["roster"]["數字"]["pin"]["code"] == "unknown_model"
      and {b["role"]: b for b in link()["bots"]}["數字"]["model_ok"] is False
      and {b["role"]: b for b in link()["bots"]}["空"]["model_ok"] is True)

print("5. through sfctl, as an AI would")
config({"寫手": {"label": "寫手", "cmd": CMD["claude"], "bot": {"memory": True}}})
r = api._execute_sfctl("bots_switch", {"role": "寫手", "cli": "", "model": "sonet"})
check("bots-set --model with a typo says which keys there are", r.get("success") is False and "opus、sonnet、haiku、fable" in r["message"], r)
r = api._execute_sfctl("bots_save", {"role": "寫手", "data": json.dumps({"model": "sonet"})})
check("bots-save too", r.get("success") is False and r.get("code") == "unknown_model", r)
r = api._execute_sfctl("bots_switch", {"role": "寫手", "cli": "", "model": "haiku"})
check("a listed key is taken", r.get("success") and stored()["寫手"]["model"] == "haiku", r)

print(f"\nResults: {passed} passed, {failed} failed")
if failed:
    sys.exit(1)
print("ALL PASS")
