"""A tab's output on its way to the page, the Telegram and LINE bridges and Frame Link.

Three loops, so that none of them can stop another:
- collect (sf-output-collect): reads every tab's buffer, hands it to the bridges' queue and
  Frame Link, and keeps what the page has not had yet. It never calls into the page and never
  runs a subprocess: a tab's startup trust prompt is answered on a thread of its own.
- push (sf-output-push): hands that text to the page (`_pushOutput`), a background tab at most
  4 times a second.
- feed (sf-bridge-feed): `feed_output` of each bridge, off both.

It used to be one loop. On 2026-11-14 it stopped inside a tmux call made while answering a
trust prompt, right after a restart, and stayed there for 21 hours: the terminal froze on that
minute, and the Telegram bridge saw no output from any tab, so the reply it was waiting for
never came, that tab counted as busy and every A2A to it was queued and given up. Nothing in
the log said so. (The call was waiting on a pipe a `tmux attach` had inherited; see
`main.close_inherited_fds`.)

A watchdog reads each loop's heartbeat. A loop silent for STALL_S has its Python stack written
to the debug log, and every thread's to the fault log, once per stall; a new loop takes its
place and the visible tab is redrawn. A stuck thread cannot be killed: the old one, if it ever
comes back, sees it was replaced and ends without handing anything on. At most MAX_REPLACED
replacements per loop an hour; past that it is only said.
"""

import faulthandler
import json
import sys
import threading
import time
import traceback
from queue import Empty

IDLE_S = 0.5                 # every loop goes round at least this often, work or not
STALL_S = 30.0               # silent this long: stuck in a call
WATCH_EVERY = 5.0
MAX_REPLACED = 6             # per loop, per hour
BG_PUSH_INTERVAL = 0.25      # a tab not on screen: at most 4 pushes a second; the one on screen at once
MAX_PUSH_CHARS = 65536       # one push into the page: a burst (a big file, base64, a long log) keeps its tail
MAX_PENDING_CHARS = 4 * MAX_PUSH_CHARS   # what waits for a page that is not taking text
LOOPS = ("collect", "push", "feed")


def tail(text: str, limit: int) -> str:
    """At most `limit` characters from the end of `text`, cut at a line break so no escape
    sequence is cut in half, and a dim line saying how much was left out."""
    if len(text) <= limit:
        return text
    dropped = len(text) - limit
    cut = text.find("\n", dropped)
    kept = text[cut + 1:] if cut != -1 else text[-limit:]
    return f"\x1b[2m…[已略過 {len(text) - len(kept)} 字元的大量輸出]…\x1b[0m\r\n" + kept


class OutputPump:
    """`api` gives sessions, _output_event, _bridge_queue, bridge, line_bridge, frame_link,
    _window, _active_sid and _auto_accept_startup_trust_prompt."""

    def __init__(self, api, log, swallow, repaint=None, fault_file=None):
        self.api = api
        self._log = log
        self._swallow = swallow
        self._repaint = repaint or (lambda: None)
        self._fault_file = fault_file or (lambda: None)
        self._lock = threading.Lock()
        self._pending = {}             # sid -> text the page has not had yet
        self._wake_push = threading.Event()
        self._trust_running, self._trust_again = set(), set()
        self._beat = {}                # loop -> monotonic time it last went round
        self._gen = {}                 # loop -> generation of the thread that is that loop now
        self._thread = {}
        self._replaced = {name: [] for name in LOOPS}
        self._said_cap = {}

    # ── threads ──────────────────────────────────────────────────────────
    def start(self):
        for name in LOOPS:
            self._spawn(name)
        threading.Thread(target=self._watch, name="sf-output-watch", daemon=True).start()

    def _spawn(self, name: str):
        gen = self._gen.get(name, 0) + 1
        self._gen[name] = gen
        self._beat[name] = time.monotonic()
        target = {"collect": self._collect, "push": self._push, "feed": self._feed}[name]
        t = threading.Thread(target=self._run, args=(name, target, gen),
                             name=f"sf-output-{name}" if name != "feed" else "sf-bridge-feed", daemon=True)
        self._thread[name] = t
        t.start()

    def _run(self, name, target, gen):
        try:
            target(gen)
        except Exception:
            # An exception that escapes a loop ends its thread; the watchdog notices the silence
            # and starts another. Said here, where the traceback still exists.
            self._log("pump", f"{name} loop ended on {traceback.format_exc(limit=6).strip()}")

    def _alive(self, name: str, gen: int) -> bool:
        if self._gen.get(name) != gen:
            return False
        self._beat[name] = time.monotonic()
        return True

    def _current(self, name: str, gen: int) -> bool:
        return self._gen.get(name) == gen

    # ── collect ──────────────────────────────────────────────────────────
    def _collect(self, gen: int):
        api = self.api
        while self._alive("collect", gen):
            api._output_event.clear()
            for sid, s in list(api.sessions.items()):
                data = s.read()
                if not data:
                    continue
                if not self._current("collect", gen):
                    return                                  # replaced while it was stuck: not ours to hand on
                if (api.bridge or api.line_bridge) and getattr(s, "_bridge_enabled", True):
                    api._bridge_queue.put_nowait((sid, data))
                # Frame Link: a tab streamed to another device gets its raw output too.
                # feed_output keeps only the tabs someone is watching, so this costs nothing otherwise.
                fl = getattr(api, "frame_link", None)
                if fl is not None:
                    try:
                        fl.feed_output(sid, data)
                    except Exception:
                        pass
                with self._lock:
                    self._pending[sid] = tail(self._pending.get(sid, "") + data, MAX_PENDING_CHARS)
                self._trust_check(sid, s)
            if self._pending:
                self._wake_push.set()
            # Reader threads set _output_event on every chunk, so this wait is only a safety net.
            api._output_event.wait(IDLE_S)

    def _trust_check(self, sid: str, s):
        """A tab's startup trust prompt is read with tmux calls; they run on a thread of their
        own, one at a time per tab, so a slow or stuck one holds up nothing else. Output that
        comes in during a check gets one more check after it: the prompt may be in it, and a
        tab waiting at a prompt prints nothing more."""
        if not getattr(s, "_startup_trust_pending", False):
            return
        with self._lock:
            if sid in self._trust_running:
                self._trust_again.add(sid)      # output came during the check: look once more after it
                return
            self._trust_running.add(sid)

        def run():
            while True:
                try:
                    self.api._auto_accept_startup_trust_prompt(sid, s)
                except Exception:
                    self._swallow("output_pump.trust")
                with self._lock:
                    if sid in self._trust_again and getattr(s, "_startup_trust_pending", False):
                        self._trust_again.discard(sid)
                        continue
                    self._trust_again.discard(sid)
                    self._trust_running.discard(sid)
                    return

        threading.Thread(target=run, name=f"sf-trust-{sid}", daemon=True).start()

    # ── push ─────────────────────────────────────────────────────────────
    def _push(self, gen: int):
        api = self.api
        last_push = {}
        while self._alive("push", gen):
            self._wake_push.clear()
            pushed = throttled = False
            win = api._window
            now = time.time()
            active = api._active_sid
            for sid in list(self._pending):
                if not win:
                    break
                if sid not in api.sessions:
                    with self._lock:
                        self._pending.pop(sid, None)        # the tab closed
                    continue
                # A background tab waits for its window; switching to it wakes this loop
                # (set_active_tab) and its text goes out at once. No tab set yet: full speed.
                if active and sid != active and now - last_push.get(sid, 0.0) < BG_PUSH_INTERVAL:
                    throttled = True
                    continue
                with self._lock:
                    chunk = self._pending.pop(sid, "")
                if not chunk:
                    continue
                chunk = tail(chunk, MAX_PUSH_CHARS)
                try:
                    # evaluate_js waits for the main thread and for xterm to write (and draw).
                    # Over 400 ms it is logged with the tab and the size, so a freeze has numbers.
                    t0 = time.time()
                    win.evaluate_js(f'_pushOutput("{sid}",{json.dumps(chunk)})')
                    dt = time.time() - t0
                    if dt > 0.4:
                        self._log("perf", f"evaluate_js 慢 {int(dt * 1000)}ms sid={sid} "
                                          f"chunk={len(chunk)}字元 tabs={len(api.sessions)}")
                    last_push[sid] = now
                    pushed = True
                except Exception:
                    with self._lock:                        # not taken: it goes first next time
                        self._pending[sid] = tail(chunk + self._pending.get(sid, ""), MAX_PENDING_CHARS)
                    self._swallow("output_pump.push")
                if not self._current("push", gen):
                    return
            # Streaming (just pushed): 5 ms to drain what is left; a throttled background tab:
            # 0.1 s to its next window; otherwise the collector wakes this loop.
            self._wake_push.wait(0.005 if pushed else (min(0.1, IDLE_S) if throttled else IDLE_S))

    # ── feed ─────────────────────────────────────────────────────────────
    def _feed(self, gen: int):
        api = self.api
        while self._alive("feed", gen):
            try:
                sid, data = api._bridge_queue.get(timeout=IDLE_S)
            except Empty:
                continue
            for bridge in (api.bridge, api.line_bridge):
                if not bridge:
                    continue
                try:
                    bridge.feed_output(sid, data)
                except Exception:
                    self._swallow("output_pump.feed")
            if not self._current("feed", gen):
                return

    # ── watchdog ─────────────────────────────────────────────────────────
    def _watch(self):
        while True:
            time.sleep(WATCH_EVERY)
            try:
                self.check()
            except Exception:
                self._swallow("output_pump.watch")

    def check(self, now: float | None = None):
        """Replace any loop silent for STALL_S. Returns the names replaced."""
        now = time.monotonic() if now is None else now
        replaced = []
        for name in LOOPS:
            silent = now - self._beat.get(name, now)
            if silent < STALL_S:
                continue
            recent = [t for t in self._replaced[name] if now - t < 3600]
            self._replaced[name] = recent
            if len(recent) >= MAX_REPLACED:
                if now - self._said_cap.get(name, -1e9) >= 3600:
                    self._said_cap[name] = now
                    self._log("pump", f"{name} loop silent {int(silent)}s; replaced {len(recent)} times "
                                      f"this hour already, not again")
                continue
            self._report(name, silent)
            self._replaced[name].append(now)
            self._spawn(name)
            replaced.append(name)
        if replaced and ("collect" in replaced or "push" in replaced):
            try:
                self._repaint()                    # what the stuck loop held is redrawn, not replayed
            except Exception:
                self._swallow("output_pump.repaint")
        return replaced

    def _report(self, name: str, silent: float):
        t = self._thread.get(name)
        frame = sys._current_frames().get(t.ident) if t is not None and t.ident else None
        where = "".join(traceback.format_stack(frame)).rstrip() if frame is not None else "(its thread has ended)"
        self._log("pump", f"{name} loop silent {int(silent)}s, a new one takes its place. It was at:\n{where}")
        f = self._fault_file()
        if f is not None:
            try:
                f.write(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} output {name} loop silent {int(silent)}s; "
                        f"every thread:\n")
                f.flush()
                faulthandler.dump_traceback(file=f, all_threads=True)
                f.flush()
            except Exception:
                self._swallow("output_pump.fault")
