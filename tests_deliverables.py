#!/usr/bin/env python3
"""Deliverables (deliverables.py): the store, the masking, and every security rule of the design's section 5.

  - the table: migrated from schema 1 (backup first), one row per (owner, ref), re-registering, states and moves,
    `seen` is not a state, counts and per-owner types, closed tabs, the 180-day prune
  - masking: KEY=value names (API_TOKEN, DB_PASSWORD), PEM and PGP private key blocks, ?token= / &key= in a URL, all
    multi-line and not truncated; a note and a source are masked BEFORE they are stored; a title is not eaten by the
    32-character rule
  - a registered file that looks like it holds secrets is registered and flagged (name, protected folder, content, a
    link into a protected folder, a URL carrying a token)
  - auto-pickup: every name in the list, every protected folder, content that matches the rules, code and config
    extensions, cache and dependency folders, old files, missing files, big files, directories, relative paths, the
    limits (5 per reply, 20 per person), no duplicates, an ignored file only when it changed
Temp folders only; `home` is given, so the real home is never looked at. Demo data only.
Run: .venv/bin/python tests_deliverables.py
"""
import os
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("SHELLFRAME_TESTS", "1")
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import deliverables as dv  # noqa: E402
import state_store as ss  # noqa: E402

passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {detail}")


TMP = Path(tempfile.mkdtemp(prefix="sf-deliv-"))
HOME = TMP / "home"
HOME.mkdir()
DB = TMP / "state.db"
NOW = 1_800_000_000.0
BOT = ("bot", "demo-eng", "示範工程師")
WORK = TMP / "work"
WORK.mkdir()


def make(rel, text="示範內容\n", age=60, base=WORK):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(text if isinstance(text, bytes) else text.encode("utf-8"))
    os.utime(p, (NOW - age, NOW - age))
    return p


def rows(**kw):
    return dv.listing(path=DB, **kw)


# ── the table and the migration ───────────────────────────────────────────────
print("the schema")
old = TMP / "old.db"
real = ss.SCHEMA
ss.SCHEMA = 1
ss._ready.discard(str(old))
ss.add("舊版的一件事", now=NOW, path=old)
ss.SCHEMA = real
ss._ready.discard(str(old))
con = ss._open(old)
have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
con.close()
check("schema 2 has the deliverables table", real >= 2 and "deliverables" in have, have)
check("an older (v1) file was copied before it was migrated, and its tasks are kept",
      (TMP / "old.db.bak-v1").exists() and ss.get(1, path=old)["title"] == "舊版的一件事"
      and sqlite3.connect(old).execute("PRAGMA user_version").fetchone()[0] == real)
cols = {r[1] for r in sqlite3.connect(old).execute("PRAGMA table_info(deliverables)")}
check("the design's columns are there", {"owner_kind", "owner_key", "owner_label", "kind", "ref", "type", "title", "note", "task_id",
                                         "state", "origin", "source", "size", "mtime", "created_at", "seen_at", "taken_at"} <= cols, cols)

# ── registering ──────────────────────────────────────────────────────────────
print("registering")
f1 = make("報告/示範週報草稿.md", "# 示範週報\n\n內容\n")
r1 = dv.add(*BOT, f1, title="示範週報草稿", note="第 3 點是我猜的", now=NOW, home=HOME, path=DB)
check("a file is registered by reference: type, size, time, state new, never opened",
      r1["state"] == "new" and r1["type"] == "md" and r1["kind"] == "file" and r1["size"] == f1.stat().st_size
      and r1["seen_at"] is None and r1["ref"] == str(f1.resolve() if False else os.path.abspath(f1)), r1)
check("only a reference is stored: no column holds the file's text",
      "示範內容" not in repr(sqlite3.connect(DB).execute("SELECT * FROM deliverables").fetchall()) and "# 示範週報" not in repr(r1))
r1b = dv.add(*BOT, f1, title="新標題", now=NOW + 5, home=HOME, path=DB)
check("one row per (owner, ref): registering it again updates it", r1b["id"] == r1["id"] and r1b["title"] == "新標題" and len(rows()) == 1)
other = dv.add("tab", "s12@1700000000", "示範分頁", f1, now=NOW, home=HOME, path=DB)
check("…but another owner's copy is its own row", other["id"] != r1["id"] and len(rows()) == 2)
dv.mark_seen(r1["id"], NOW + 6, path=DB)
check("opening it makes it seen: still new (undecided), no longer counted as new",
      dv.get(r1["id"], DB)["state"] == "new" and dv.get(r1["id"], DB)["seen_at"] == NOW + 6)
ov = dv.overview({"s12": "1700000000"}, path=DB)
check("overview: the red number counts new and never opened (the other owner's)", ov["new"] == 1, ov)
check("overview: per owner, the bot's desk and an open tab's desk, key by key",
      ov["by_owner"]["b:demo-eng"]["count"] == 1 and ov["by_owner"]["b:demo-eng"]["new"] == 0
      and ov["by_owner"]["t:s12"]["new"] == 1 and ov["by_owner"]["t:s12"]["types"] == ["md"], ov["by_owner"])
ov2 = dv.overview({"s12": "1700000999"}, path=DB)
check("a tab id reused by a newer tab (another opened time): the old tab's row is not on the new tab's desk, it is closed",
      "t:s12" not in ov2["by_owner"] and ov2["by_owner"]["x:s12@1700000000"]["closed"] and ov2["new"] == 1, ov2["by_owner"].keys())
check("the red number counts a closed tab's new ones", ov2["new"] == 1)

print("states and moves")
a = dv.move(r1["id"], "keep", NOW + 7, path=DB)
check("先放著: kept, still on the desk, counted in count but not in new",
      a["state"] == "kept" and dv.overview({}, path=DB)["by_owner"]["b:demo-eng"]["count"] == 1)
again = dv.add(*BOT, f1, now=NOW + 8, home=HOME, path=DB)
check("registering a kept one again (file unchanged) leaves it kept", again["state"] == "kept")
os.utime(f1, (NOW + 100, NOW + 100))
again = dv.add(*BOT, f1, now=NOW + 9, home=HOME, path=DB)
check("…a changed file comes back as new and unopened", again["state"] == "new" and again["seen_at"] is None)
t = dv.move(r1["id"], "take", NOW + 10, path=DB)
check("拿走: taken, off the desk, still in the history (listing), not counted",
      t["state"] == "taken" and t["taken_at"] == NOW + 10 and "b:demo-eng" not in dv.overview({}, path=DB)["by_owner"]
      and [x["id"] for x in rows(states=("taken",))] == [r1["id"]])
check("a taken one is found by search (title)", [x["id"] for x in rows(query="新標題")] == [r1["id"]])
back = dv.add(*BOT, f1, title="再交一次", now=NOW + 11, home=HOME, path=DB)
check("registering a taken one again puts it back on the desk as new", back["state"] == "new" and back["taken_at"] is None)


def refuses(fn, *exc):
    try:
        fn()
    except exc as e:
        return e
    except Exception as e:                                             # noqa: BLE001
        return f"wrong error {e!r}"
    return None


check("'accept' of something that is not a candidate is refused", refuses(lambda: dv.move(r1["id"], "accept", path=DB), ValueError))
check("a file that does not exist cannot be registered", refuses(lambda: dv.add(*BOT, str(WORK / "沒有.md"), path=DB), ValueError))
check("a task that does not exist is refused", refuses(lambda: dv.add(*BOT, f1, task_id=99, path=DB), ss.NotFound))
tk = ss.add("示範任務", now=NOW, path=DB)
linked = dv.add(*BOT, f1, task_id=tk["id"], now=NOW, home=HOME, path=DB)
check("--task links it; overview counts per task", linked["task_id"] == tk["id"] and dv.overview({}, path=DB)["by_task"] == {str(tk["id"]): 1})
link = dv.add(*BOT, "https://demo.example.test/preview/login", title="示範預覽站", now=NOW, home=HOME, path=DB)
check("a URL: kind url, type url, nothing to stat", link["kind"] == "url" and link["type"] == "url" and link["size"] == 0 and not link["flag"])
check("a directory: type folder", dv.add(*BOT, str(WORK / "報告"), now=NOW, home=HOME, path=DB)["type"] == "folder")
for name, typ in (("a.pdf", "pdf"), ("a.HTML", "html"), ("a.zip", "zip"), ("a.txt", "md"), ("a.JPG", "img"), ("a.key", "pptx"),
                  ("a.csv", "xlsx"), ("a.doc", "docx"), ("a.mov", "video"), ("a.xyz", "file"), ("noext", "file")):
    check(f"{name} draws as {typ}", dv.type_of(name) == typ, dv.type_of(name))

print("closing a tab, the 180 days")
tk_old = dv.add(*BOT, make("舊.md"), now=NOW - 200 * 86400, home=HOME, path=DB)
dv.move(tk_old["id"], "take", NOW - 200 * 86400, path=DB)
con = ss._open(DB)
ss.maintain(NOW, path=DB)
check("the daily upkeep removes a taken row 180 days after it was taken, never a file",
      not [x for x in rows(states=("taken",)) if x["id"] == tk_old["id"]] and (WORK / "舊.md").exists())
fresh = dv.add(*BOT, make("新拿走.md"), now=NOW - 10 * 86400, home=HOME, path=DB)
dv.move(fresh["id"], "take", NOW - 10 * 86400, path=DB)
ss.maintain(NOW + 2 * 86400, path=DB)
check("a row taken 10 days ago stays", any(x["id"] == fresh["id"] for x in rows(states=("taken",))))
con.close()

# ── masking ──────────────────────────────────────────────────────────────────
print("masking")
SECRETS = ["hunter22", "s3cretpw", "ghp_demoabcdefghijkl", "abc123xyz", "topsecretvalue", "BEGINKEYBODY", "q1w2e3", "zz9"]
text = ("# 測試站\n\nAPI_TOKEN=abc123xyz\nDB_PASSWORD=s3cretpw\nexport MY_SECRET_KEY='topsecretvalue'\n\n"
        "https://staging.example.test/login?token=zz9&page=2\nhttps://api.example.test/v1?x=1&key=q1w2e3&y=2\n\n"
        "-----BEGIN OPENSSH PRIVATE KEY-----\nBEGINKEYBODY\nline two\n-----END OPENSSH PRIVATE KEY-----\n\n"
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\nBEGINKEYBODY\n-----END PGP PRIVATE KEY BLOCK-----\n\n"
        "https://demo:hunter22@git.example.test/r.git\nghp_demoabcdefghijkl\n\n最後一行正常的字。\n")
m = dv.mask(text)
check("KEY=value names (API_TOKEN, DB_PASSWORD, a quoted one), value of any length", "API_TOKEN=<redacted>" in m
      and "DB_PASSWORD=<redacted>" in m and "topsecretvalue" not in m, m[:300])
check("?token= and &key= in a URL, the rest of the URL stays", "?token=<redacted>&page=2" in m and "&key=<redacted>&y=2" in m, m)
check("a PEM private key block and a PGP one, the whole block", "BEGINKEYBODY" not in m and "line two" not in m)
check("user:password@ in a URL, a ghp_ token", "hunter22" not in m and "ghp_demoabcdefghijkl" not in m)
check("none of the planted secrets survives", not [s for s in SECRETS if s in m], [s for s in SECRETS if s in m])
check("multi-line: the line breaks are kept (not one line)", m.count("\n") >= text.count("\n") - 8 and "最後一行正常的字。" in m and "\n" in m)
long_doc = "段落\n" * 5000 + "API_TOKEN=lateone\n"
check("not truncated: a secret at the end of a long text is still masked and the text is whole",
      dv.mask(long_doc).endswith("API_TOKEN=<redacted>\n") and len(dv.mask(long_doc)) > 15000)
check("a truncated PEM block (no END line) masks to the end", "tail" not in dv.mask("a\n-----BEGIN RSA PRIVATE KEY-----\nbody\ntail"))
check("masking twice changes nothing more", dv.mask(m) == m)
check("a title is not eaten by the 32-character rule (a long slug stays readable)",
      dv.mask_line("demo-quarterly-report-2026-11-19-v2-final", 200, strict=False) == "demo-quarterly-report-2026-11-19-v2-final")
check("…while body text is (a 40-character key run)", "<redacted>" in dv.mask("key " + "a" * 40))
r = dv.add(*BOT, make("n.md"), title="t", note="密碼 API_TOKEN=abc123xyz 與 https://x.test/?token=zz9", source="見 ghp_demoabcdefghijkl 那行",
           now=NOW, home=HOME, path=DB)
raw = repr(sqlite3.connect(DB).execute("SELECT note, source, title FROM deliverables WHERE id=?", (r["id"],)).fetchall())
check("--note and the source sentence are masked BEFORE they are stored (the database never holds the secret)",
      "abc123xyz" not in raw and "zz9" not in raw and "ghp_demoabcdefghijkl" not in raw and "<redacted>" in raw, raw)
check("a note is one line and capped", "\n" not in dv.mask_line("a\nb\n" * 400, dv.NOTE_MAX) and len(dv.mask_line("x " * 400, dv.NOTE_MAX)) <= dv.NOTE_MAX)

# ── a registered file that looks like it holds secrets ──────────────────────────
print("registered, but flagged")
env = make(".env", "API_TOKEN=abc123xyz\n", base=WORK)
check("a .env file the bot registered: registered, flagged", dv.add(*BOT, env, now=NOW, home=HOME, path=DB)["flag"] == "secret")
for name in ("notes-password.txt", "我的 Token.md", "x.pem"):
    check(f"{name}: flagged by its name", dv.add(*BOT, make(name), now=NOW, home=HOME, path=DB)["flag"] == "secret")
check("a text file whose content matches the rules: flagged",
      dv.add(*BOT, make("ok-name.md", "連線: API_TOKEN=abc123xyz\n"), now=NOW, home=HOME, path=DB)["flag"] == "secret")
check("a harmless file is not flagged", dv.add(*BOT, make("harmless.md", "只是一段話\n"), now=NOW, home=HOME, path=DB)["flag"] == "")
ssh = make(".ssh/notes.md", "hello", base=HOME)
check("a file under ~/.ssh: flagged", dv.add(*BOT, ssh, now=NOW, home=HOME, path=DB)["flag"] == "secret")
lnk = WORK / "innocent.md"
os.symlink(ssh, lnk)
check("a link in a normal folder that points into ~/.ssh: flagged (the real path is what counts)",
      dv.add(*BOT, str(lnk), now=NOW, home=HOME, path=DB)["flag"] == "secret")
check("a URL that carries a token: flagged", dv.add(*BOT, "https://demo.example.test/x?token=zz9", now=NOW, home=HOME, path=DB)["flag"] == "secret")
check("an image is never text-scanned (a PNG whose bytes look like a key is not flagged)",
      dv.add(*BOT, make("shot.png", b"\x89PNG\r\n\0API_TOKEN=abc123xyz"), now=NOW, home=HOME, path=DB)["flag"] == "")

# ── auto-pickup ──────────────────────────────────────────────────────────────
print("auto-pickup: what is offered")


def found(text, **kw):
    return dv.find_candidates(text, now=NOW, home=HOME, **kw)


good = make("out/示範週報.pdf", b"%PDF-1.4 fake", age=300)
res = found(f"報告好了，存在 {good}，請看。")
check("an absolute path in a reply, the file exists and is recent: a candidate with its type, size and the sentence",
      [c["ref"] for c in res["candidates"]] == [str(good)] and res["candidates"][0]["type"] == "pdf"
      and "報告好了" in res["candidates"][0]["source"], res)
tilde_dir = HOME / "demo-proj"
tilde = make("摘要.md", "# 摘要\n", age=100, base=tilde_dir)
os.environ["HOME"] = str(HOME)
res = found("整理好了 ~/demo-proj/摘要.md，第二段是我猜的。")
check("a ~/ path (home given by the environment) works too", [c["title"] for c in res["candidates"]] == ["摘要.md"], res)
check("fullwidth punctuation around a path and a closing full stop do not break it",
      [c["title"] for c in found(f"（{good}）。檔案在：`{good}`.")["candidates"]] == ["示範週報.pdf"])
check("a relative path is not a candidate", found("see out/示範週報.pdf and ./a.md")["candidates"] == [])
check("a path that does not exist is not", found("/srv/demo/not-there.pdf")["candidates"] == [])
check("a directory is not", found(str(WORK / "out"))["candidates"] == [])
old_f = make("old/舊筆記.md", "x", age=3 * 3600)
check("a file not modified in the last 2 hours is an old note, not a deliverable", found(str(old_f))["candidates"] == [])
edge = make("old/兩小時內.md", "x", age=7100)
check("…one modified 7,100 s ago still is", len(found(str(edge))["candidates"]) == 1)
big = make("big/大檔.zip", b"x", age=30)
check("a small regular file is fine", len(found(str(big))["candidates"]) == 1)
orig_max = dv.AUTO_SIZE_MAX
dv.AUTO_SIZE_MAX = 0
check("…the size limit applies", found(str(big))["candidates"] == [])
dv.AUTO_SIZE_MAX = orig_max
for d in ("node_modules/x", ".git/hooks", ".venv/lib", "site-packages/p", ".cache/c", "Library/Caches/app"):
    p = make(f"{d}/報告.pdf", b"%PDF", age=30)
    check(f"inside {d}: not offered", found(str(p))["candidates"] == [], str(p))
for ext in ("py", "js", "ts", "sh", "css", "json", "xml", "yaml", "yml"):
    p = make(f"code/示範.{ext}", "x = 1\n", age=30)
    check(f".{ext} (code or config) is never offered", found(str(p))["candidates"] == [])
check("an extension outside the list is not offered either", found(str(make("x/data.bin", b"\0", age=30)))["candidates"] == [])

print("auto-pickup: key and config files are never listed")
names = [".env", ".env.local", ".env.production", "prod.env", "server.pem", "x.key", "a.p12", "a.pfx", "a.jks", "a.keystore",
         "id_rsa", "id_rsa.pub", "id_ed25519", "id_ed25519.pub", "a.ppk", ".netrc", "_netrc", ".npmrc", ".pypirc", ".git-credentials",
         ".htpasswd", "my-token.md", "Secret.txt", "credentials.csv", "login-PASSWORD.pdf", "passwd.txt", "a.keychain", "a.keychain-db",
         "vault.kdbx", "ID_RSA", "TOKEN.MD"]
for n in names:
    p = make(f"names/{n}", "just words\n", age=30)
    r = found(str(p))
    check(f"{n}: skipped, and its name is reported (not listed)", r["candidates"] == [] and r["skipped"] == [n], r)
home_dirs = [".ssh", ".aws", ".gnupg", ".kube", "Library/Keychains", ".config", ".claude"]
for d in home_dirs:
    p = make(f"{d}/報告.md", "just words\n", age=30, base=HOME)
    check(f"under ~/{d}: never listed", found(str(p))["candidates"] == [] and found(str(p))["skipped"] == ["報告.md"])
mine = make("cfg/報告.md", "just words\n", age=30)
check("ShellBot's own config folder: never listed", found(str(mine), protected=[WORK / "cfg"])["candidates"] == [])
import sf_config  # noqa: E402
own = make("cfg-own/報告.md", "just words\n", age=30, base=Path(sf_config.config_dir()))
check("…the real one (sf_config.config_dir) is protected without being told", found(str(own))["candidates"] == [])
check("a link in a normal folder into ~/.ssh is not listed", found(str(lnk))["candidates"] == [] and found(str(lnk))["skipped"] == ["innocent.md"])
check("the case of the folder name does not matter (~/.SSH)", found(str(make(".SSH/x.md", "w", age=30, base=HOME)))["candidates"] == [])
for body, label in (("API_TOKEN=abc123xyz\n", "KEY=value"), ("a: https://x.test/?token=zz9\n", "?token="),
                    ("-----BEGIN PGP PRIVATE KEY BLOCK-----\nx\n", "PGP block"), ("password: hunter22\n", "password:"),
                    ("sk-demo1234567890abcdef\n", "sk- key"), ("a" * 40 + "\n", "32+ run")):
    p = make(f"content/{abs(hash(label))}.md", body, age=30)
    check(f"a text file whose content has a {label}: skipped, not listed", found(str(p))["candidates"] == [] and found(str(p))["skipped"], label)
check("a text file just over 256 KB is not scanned (and so is listed)", len(found(str(make("content/big.md", "字" * 90000 + "\nAPI_TOKEN=x\n", age=30)))["candidates"]) == 1)
check("a PDF is not text-scanned: its bytes can look like a key", len(found(str(make("content/key.pdf", b"%PDF API_TOKEN=abc123xyz", age=30)))["candidates"]) == 1)
check("an html file is scanned too", found(str(make("content/p.html", "<p>API_TOKEN=abc123xyz</p>", age=30)))["candidates"] == [])

print("auto-pickup: limits and the candidate list")
many = [make(f"many/m{i}.md", f"# {i}\n", age=10 + i) for i in range(8)]
res = found(" ".join(str(p) for p in many))
check("at most 5 from one reply, in the order they appear", [c["title"] for c in res["candidates"]] == [f"m{i}.md" for i in range(5)], res)
D = TMP / "limits.db"
who = ("bot", "demo-res", "示範研究員")
n = dv.record_candidates(*who, res, now=NOW, home=HOME, path=D)
check("they are stored as candidates: not on the desk, not counted",
      n == 5 and len(dv.listing(states=("candidate",), path=D)) == 5 and dv.overview({}, path=D)["new"] == 0
      and dv.overview({}, path=D)["by_owner"] == {} and dv.overview({}, path=D)["candidates_total"] == 5)
check("the same reply again adds nothing (no duplicates)", dv.record_candidates(*who, res, now=NOW, home=HOME, path=D) == 0)
c0 = dv.listing(states=("candidate",), path=D)[-1]
acc = dv.move(c0["id"], "accept", NOW + 1, path=D)
check("收下: it is on the desk, as new and unopened", acc["state"] == "new" and acc["seen_at"] is None and dv.overview({}, path=D)["new"] == 1)
check("…a file already on the desk is not offered again", dv.record_candidates(*who, found(str(many[0])), now=NOW, home=HOME, path=D) == 0)
c1 = dv.listing(states=("candidate",), path=D)[0]
dv.move(c1["id"], "dismiss", NOW + 2, path=D)
check("忽略: not asked again", dv.record_candidates(*who, found(c1["ref"]), now=NOW + 3, home=HOME, path=D) == 0
      and dv.get(c1["id"], D)["state"] == "dismissed")
os.utime(c1["ref"], (NOW - 5, NOW - 5))
dv.record_candidates(*who, found(c1["ref"]), now=NOW + 4, home=HOME, path=D)
c1b = dv.get(c1["id"], D)
check("an ignored file that changed is offered again", c1b["state"] == "candidate", c1b["state"])
dv.dismiss_candidates(("bot", "demo-res"), NOW + 5, path=D)
check("全部忽略: every candidate of that person", not dv.listing(states=("candidate",), path=D))
many2 = [make(f"many2/m{i}.md", f"# {i}\n", age=10 + i) for i in range(30)]
for i in range(0, 30, 5):
    dv.record_candidates(*who, found(" ".join(str(p) for p in many2[i:i + 5])), now=NOW + 10 + i, home=HOME, path=D)
left = dv.listing(states=("candidate",), path=D)
check("at most 20 candidates per person: the oldest go", len(left) == 20 and {Path(c["ref"]).name for c in left} == {f"m{i}.md" for i in range(10, 30)},
      sorted(Path(c["ref"]).name for c in left))
other_who = ("tab", "s3@1", "示範分頁")
check("…the limit is per person", dv.record_candidates(*other_who, found(str(many2[0])), now=NOW, home=HOME, path=D) == 1)
check("the names of key-looking files it passed over are kept per person (names only)",
      dv.skipped_names(("bot", "demo-res"), D) == [] and dv.record_candidates(*who, found(str(make("names/.env", "x", age=30))), now=NOW, home=HOME, path=D) == 0
      and dv.skipped_names(("bot", "demo-res"), D) == [".env"])

print("a Telegram path detector is the one used")
from bridge_telegram import TelegramBridge  # noqa: E402
check("the path regex is the bridge's own", dv._path_regex() is TelegramBridge._FILE_PATH_RE)

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
