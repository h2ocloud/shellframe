"""Api mixin — scroll-history overlay domain（God-class 分批拆解 第一批）.

上滾歷史的完整鏈路：transcript 來源（AI 分頁 source of truth）→ pyte
alt-screen 重建 → tmux capture，共用 _dedupe_history_lines 去重管線；
含 history_audit 自檢與 pyte SGR 樣式重建。行為與 main.py 內時期
byte-identical，僅搬家。回歸測試：tests_history_dedup.py。
"""

import json
import os
import re
import subprocess
import time
import unicodedata
from pathlib import Path

import agent_status
from sf_log import _dlog, _swallow  # noqa: F401


class HistoryApiMixin:
    # Catches more than just the basic CSI form so dedup keys really do
    # ignore styling. Old regex only matched `ESC [ digits;... letter`,
    # which left OSC hyperlinks (ESC ] ... BEL/ST), charset designates,
    # and CSI sequences ending in `~` / `?` / `>` in the "stripped"
    # string. Two visually-identical lines with different residual
    # escapes ended up as different dedup keys → duplicate survived.
    _ANSI_STRIP_RE = re.compile(
        r'\x1b\[[0-9;?!<>=]*[@-~]'         # CSI with any final byte
        r'|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)'  # OSC … BEL or ST
        r'|\x1b[()][A-Z0-9]'                # designate G0/G1
        r'|\x1b[=>78cN]'                    # short single-char escapes
    )

    _NORM_WHITESPACE_RE = re.compile(r'\s+')

    @staticmethod
    def _visual_width(s: str) -> int:
        """Approx terminal cell width. CJK/fullwidth count 2, control 0,
        everything else 1. Used for dedup thresholds so CJK lines (4 chars
        = 8 cells) aren't mis-treated as 'too short to dedup'."""
        w = 0
        for ch in s:
            o = ord(ch)
            if o < 0x20 or o == 0x7f:
                continue
            ea = unicodedata.east_asian_width(ch)
            w += 2 if ea in ("W", "F") else 1
        return w

    # pyte stores Char colors as NAMES ("red", "brightblack" — including
    # pyte's own "brown" for yellow and the upstream "bfightmagenta" typo)
    # or bare hex ("ff8700") for 256/true-color. Map names back to SGR so
    # the overlay can re-style pyte history the same way tmux -e does.
    _PYTE_FG_SGR = {
        'black': '30', 'red': '31', 'green': '32', 'brown': '33',
        'blue': '34', 'magenta': '35', 'cyan': '36', 'white': '37',
        'brightblack': '90', 'brightred': '91', 'brightgreen': '92',
        'brightbrown': '93', 'brightblue': '94', 'brightmagenta': '95',
        'brightcyan': '96', 'brightwhite': '97',
    }

    _PYTE_BG_SGR = {
        'black': '40', 'red': '41', 'green': '42', 'brown': '43',
        'blue': '44', 'magenta': '45', 'cyan': '46', 'white': '47',
        'brightblack': '100', 'brightred': '101', 'brightgreen': '102',
        'brightbrown': '103', 'brightblue': '104', 'brightmagenta': '105',
        # NOT a typo of ours: pyte 0.8.2's graphics.BG_AIXTERM[105] really is
        # the misspelled string 'bfightmagenta', so Char.bg carries it and the
        # map must translate it. Removing this key (v0.23.0 rc) silently
        # dropped SGR 105 backgrounds from the pyte source.
        'bfightmagenta': '105',
        'brightcyan': '106', 'brightwhite': '107',
    }

    @classmethod
    def _pyte_char_sgr(cls, c):
        """(sgr_params, paints_bg) for one pyte Char. sgr_params is ''
        when the char is unstyled; paints_bg says whether a trailing
        space in this style is visible (bg color / reverse video) and
        therefore must survive rstrip.

        Fidelity vs tmux -e: everything pyte models is reconstructed
        (bold/italics/underscore/blink/reverse/strikethrough, 16-color
        names, 256/truecolor as hex). KNOWN GAP — dim (SGR 2): pyte
        0.8.2's Char has no faint field, the attribute is dropped at
        parse time, so dim text (Claude Code's muted grays) renders at
        full brightness from the pyte source. Unfixable at this layer;
        if a "亮度不對" report carries source=pyte, this is why."""
        parts = []
        paints = False
        if getattr(c, 'bold', False):
            parts.append('1')
        if getattr(c, 'italics', False):
            parts.append('3')
        if getattr(c, 'underscore', False):
            parts.append('4')
        if getattr(c, 'blink', False):
            parts.append('5')
        if getattr(c, 'reverse', False):
            parts.append('7')
            paints = True
        if getattr(c, 'strikethrough', False):
            parts.append('9')
        fg = getattr(c, 'fg', 'default') or 'default'
        if fg != 'default':
            if fg in cls._PYTE_FG_SGR:
                parts.append(cls._PYTE_FG_SGR[fg])
            elif len(fg) == 6:
                try:
                    parts.append('38;2;%d;%d;%d' % (
                        int(fg[0:2], 16), int(fg[2:4], 16), int(fg[4:6], 16)))
                except ValueError:
                    pass
        bg = getattr(c, 'bg', 'default') or 'default'
        if bg != 'default':
            if bg in cls._PYTE_BG_SGR:
                parts.append(cls._PYTE_BG_SGR[bg])
                paints = True
            elif len(bg) == 6:
                try:
                    parts.append('48;2;%d;%d;%d' % (
                        int(bg[0:2], 16), int(bg[2:4], 16), int(bg[4:6], 16)))
                    paints = True
                except ValueError:
                    pass
        return ';'.join(parts), paints

    @classmethod
    def _pyte_row_text(cls, row, cols, ansi):
        """Render one pyte buffer row (StaticDefaultDict col -> Char).

        NOTE: a pyte history row is a DICT — iterating it yields int
        column keys, not Chars. The old `for c in row` + getattr(c,
        'data', ' ') therefore rendered every history row as pure
        spaces, so the alt-screen pyte source always failed the length
        gate and silently fell through to tmux (whose normal-screen
        scrollback doesn't have the alt-screen content — the exact
        "上滾看到不對的歷史" complaint). Index by column instead.
        """
        cells = []
        for col in range(cols):
            try:
                c = row[col]
            except Exception:
                break
            ch = getattr(c, 'data', ' ')
            if ch == '':
                # shadow cell of a wide (CJK) char — pyte stores data=""
                # in the column after it; emitting ' ' here would inject
                # a space inside every CJK word ("橘 色").
                continue
            if ch is None:
                ch = ' '
            if ansi:
                sgr, paints = cls._pyte_char_sgr(c)
            else:
                sgr, paints = '', False
            cells.append((sgr, paints, ch))
        # rstrip, but keep trailing spaces that paint a visible background
        while cells and cells[-1][2] == ' ' and not cells[-1][1]:
            cells.pop()
        out = []
        cur = ''
        for sgr, _, ch in cells:
            if ansi and sgr != cur:
                out.append('\x1b[0m' if not sgr else '\x1b[0;' + sgr + 'm')
                cur = sgr
            out.append(ch)
        if cur:
            out.append('\x1b[0m')
        return ''.join(out)

    @classmethod
    def _pyte_history_text(cls, slot, ansi: bool = False) -> str:
        """Render a bridge slot's pyte buffer as text — scrollback history
        followed by the current visible screen. With ansi=True each row
        carries SGR codes rebuilt from pyte Char attributes, so the
        overlay shows the same colors tmux -e would give.

        pyte.HistoryScreen exposes:
          - screen.history.top   deque of {col: Char} rows (older rows)
          - screen.history.bottom deque (after-current rows; usually empty)
          - screen.buffer        {row: {col: Char}} for the current screen
          - screen.display       list[str] of currently rendered rows

        Raises nothing — caller falls back to tmux on any failure.
        """
        try:
            cols = slot.screen.columns
        except Exception:
            cols = 200
        out = []  # list of (plain_for_trim, rendered)
        try:
            top = slot.screen.history.top
        except Exception:
            top = []
        for row in top:
            try:
                rendered = cls._pyte_row_text(row, cols, ansi)
            except Exception:
                continue
            out.append((cls._ANSI_STRIP_RE.sub('', rendered) if ansi else rendered, rendered))
        if ansi:
            # screen.display is plain strings — restyle from screen.buffer
            try:
                buf = slot.screen.buffer
                lines = slot.screen.lines
            except Exception:
                buf, lines = {}, 0
            for y in range(lines):
                row = buf.get(y) if hasattr(buf, 'get') else None
                if row is None:
                    out.append(('', ''))
                    continue
                try:
                    rendered = cls._pyte_row_text(row, cols, True)
                except Exception:
                    continue
                out.append((cls._ANSI_STRIP_RE.sub('', rendered), rendered))
        else:
            try:
                display = slot.screen.display
            except Exception:
                display = []
            for row in display:
                if isinstance(row, str):
                    out.append((row.rstrip(), row.rstrip()))
                else:
                    try:
                        rendered = cls._pyte_row_text(row, cols, False)
                        out.append((rendered, rendered))
                    except Exception:
                        pass
        # Drop trailing blank rows (pyte pads display to its rows count).
        while out and not out[-1][0].strip():
            out.pop()
        # Drop LEADING blank rows. pyte pre-allocates a 50-row grid the
        # moment the screen is created, so when the bridge starts feeding
        # mid-conversation the display's top half can be all blanks
        # (cursor lives near the bottom). Without this trim the overlay
        # opens to a wall of empty space — Howard saw "上面不見了 / 整段
        #空白才出現條目". Internal blank lines (between paragraphs) are
        # preserved; only the top contiguous run is dropped.
        while out and not out[0][0].strip():
            out.pop(0)
        return '\n'.join(rendered for _, rendered in out)

    _NORM_NOSPACE_RE = re.compile(r'\s+')

    @classmethod
    def _collapse_redraw_frames(cls, lines, win: int = 100, min_drop: int = 15,
                                max_iter: int = 12):
        """Drop terminal-resize redraw frames from a tmux capture.

        A TUI re-rendering streaming content at a new width leaves several
        wrap-variants of the same logical block in tmux scrollback. They
        share an identical *character* stream (whitespace removed) but wrap
        at different points, defeating line-level dedup.

        Detection is anchor-based and wrap-invariant: normalize each line
        (strip ANSI + remove ALL whitespace), concatenate into one stream,
        and look for a `win`-char window that starts at one line boundary
        and recurs at a LATER line boundary. Each redraw frame restarts its
        blocks on fresh lines, so the recurrence marks a redraw boundary.
        Keep everything before the FIRST occurrence and everything from the
        LAST occurrence onward; the dropped middle is stale partial frames
        (the final frame is the current-width, most-complete render).

        Guards against nuking legitimate repeats:
          • win=100 normalized chars ≈ a full line of code/CJK — an
            accidental 100-char recurrence at a line start is vanishingly
            unlikely (a genuine repeated long command is the only realistic
            case, and keeping its last copy is still correct).
          • min_drop: only collapse when ≥15 lines sit between the first and
            last occurrence, so short echoes are never touched.
        Runs in O(lines) per pass via a line-start window index.
        """
        def _norm(s):
            return cls._NORM_NOSPACE_RE.sub('', cls._ANSI_STRIP_RE.sub('', s))

        for _ in range(max_iter):
            sigs = [_norm(l) for l in lines]
            starts = []
            off = 0
            for ns in sigs:
                starts.append(off)
                off += len(ns)
            total = off
            if total < win * 2:
                break
            N = ''.join(sigs)
            from collections import defaultdict
            idx = defaultdict(list)
            for li, (st, ns) in enumerate(zip(starts, sigs)):
                if len(ns) < 8 or st + win > total:
                    continue
                idx[N[st:st + win]].append(li)
            changed = False
            for li, (st, ns) in enumerate(zip(starts, sigs)):
                if len(ns) < 8 or st + win > total:
                    continue
                occ = idx.get(N[st:st + win])
                if not occ or occ[-1] <= li:
                    continue
                l_last = occ[-1]
                if l_last - li < min_drop:
                    continue
                # Verify this is a real redraw frame, not two far-apart
                # occurrences of a common UI element. A `────` separator
                # (≥100 identical chars) or a repeated `⏺ Bash(...` tool
                # header recurs at unrelated points; the bare anchor match
                # then dropped EVERYTHING between the first and last
                # occurrence — nuking the whole conversation and leaving just
                # the banner (Howard: 上滾只剩 banner、沒對話). Only collapse
                # when the dropped block's char stream actually re-appears
                # immediately after l_last, i.e. the same frame really was
                # re-rendered at a new width.
                a = starts[li]
                b = starts[l_last]
                if not N[b:].startswith(N[a:b]):
                    continue
                lines = lines[:li] + lines[l_last:]
                changed = True
                break
            if not changed:
                break
        return lines

    @staticmethod
    def _cjk_cells(s: str) -> int:
        """Count visual cells contributed by CJK/fullwidth chars only. Used
        to gate the non-consecutive dedup pass: we only want to collapse
        duplicates that look like Claude Code's streaming CJK redraw, NOT
        legitimate repeats in source code (`return null;` appearing three
        times should stay three times)."""
        cells = 0
        for ch in s:
            if unicodedata.east_asian_width(ch) in ("W", "F"):
                cells += 2
        return cells

    def get_clean_history(self, sid: str, max_lines: int = 10000, ansi: bool = True) -> str:
        """Return scroll-back history for the overlay, with streaming
        redraw noise collapsed.

        Strategy (v0.11.60 — fixed "上滾看到不對的歷史"):

        Claude Code / Codex / vim all enter the ALTERNATE screen buffer
        via `\\x1b[?1049h` when they start their TUI. In alt-screen mode,
        `tmux capture-pane -S -N` returns the NORMAL-screen scrollback —
        i.e. whatever was on the terminal BEFORE the alt-screen was
        entered, NOT the rows that just scrolled out of the alt-screen
        viewport during the current long reply. So when a user's single
        reply ran longer than one screen and they scrolled up to read the
        beginning, the overlay would dutifully show the previous shell
        prompt / unrelated history — Howard's exact complaint.

        Fix: detect alt-screen via `#{alternate_on}` and switch source.
        pyte's HistoryScreen, fed by the bridge directly from PTY bytes,
        IS aware of alt-screen line-feeds (its `history.top` deque accrues
        rows regardless of which buffer is active), so it has the actual
        recent reply content. Outside alt-screen mode tmux is still
        primary — pyte's history cap is smaller. Both sources can now
        carry ANSI styling (tmux via `-e`, pyte via Char-attribute
        reconstruction) so the overlay renders colors either way.
        """
        s = self.sessions.get(sid)
        if not s or not getattr(s, '_tmux_name', None):
            # tmux unavailable — fall back to pyte if the bridge has a slot
            # for this session. Better than nothing on Windows / no-tmux.
            return self._pyte_fallback_response(sid, ansi=ansi)

        # AI tabs (claude/codex): the transcript JSONL is the source of
        # truth for the conversation — no redraw frames, no wrap variants,
        # nothing for dedup heuristics to fight. This sidesteps the whole
        # terminal-frame-reconstruction class of bugs（十多輪「上滾重複/樣式
        # 錯亂」的戰線）. Sparse/unmapped transcripts fall through to the
        # terminal pipeline below; non-AI tabs never enter this path.
        try:
            resp = self._transcript_history_response(s, sid, ansi)
            if resp:
                return resp
        except Exception:
            _swallow(f"get_clean_history.transcript:{sid}")

        # Probe alt-screen state. Cheap: a single `display-message`. If it
        # fails (shouldn't, since we just verified _tmux_name) we proceed
        # as if normal-screen — tmux capture is the safe default.
        in_alt_screen = False
        try:
            r_alt = subprocess.run(
                ["tmux", "display-message", "-p", "-t", s._tmux_name,
                 "#{alternate_on}"],
                capture_output=True, text=True, timeout=2,
            )
            in_alt_screen = (r_alt.stdout.strip() == "1")
        except Exception:
            _swallow("Api.get_clean_history:3563")

        if in_alt_screen:
            slot = None
            for candidate_bridge in (self.bridge, self.line_bridge):
                if candidate_bridge is None:
                    continue
                try:
                    slot = candidate_bridge.slots.get(sid)
                except Exception:
                    slot = None
                if slot is not None:
                    break
            if slot is not None and getattr(slot, 'screen', None) is not None:
                try:
                    text = self._pyte_history_text(slot, ansi=ansi)
                except Exception:
                    text = ""
                # Same cleaning pipeline as the tmux path. pyte's history.top
                # accrues a copy of every full-viewport redraw (resize, panel
                # toggle, the TUI re-rendering the conversation), so WITHOUT
                # this the alt-screen source — the one every Claude/Codex tab
                # actually uses — showed raw duplicate frames while all the
                # dedup work only ever ran on the tmux branch.
                if text:
                    text = self._dedupe_history_lines(text.split("\n"), ansi)
                # Length gate on the PLAIN text — SGR bytes would let a
                # nearly-empty styled capture pass.
                plain = self._ANSI_STRIP_RE.sub('', text) if ansi else text
                if plain and len(plain) > 64:
                    return json.dumps({
                        "success": True,
                        "text": text,
                        "ansi": ansi,
                        "source": "pyte (alt-screen)",
                    })
                # pyte empty/too-short → fall through to tmux. Better than
                # blocking the overlay with "no history".
        try:
            cmd = ["tmux", "capture-pane", "-p", "-J", "-t", s._tmux_name,
                   "-S", f"-{max_lines}"]
            if ansi:
                cmd.append("-e")
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
            if r.returncode != 0:
                return json.dumps({"success": False, "reason": r.stderr[-200:], "text": ""})
            text = self._dedupe_history_lines(r.stdout.split("\n"), ansi)
            return json.dumps({
                "success": True,
                "text": text,
                "ansi": ansi,
                "source": f"tmux ({'normal' if not in_alt_screen else 'alt-fallback'})",
            })
        except Exception as e:
            return json.dumps({"success": False, "reason": str(e), "text": ""})

    def _dedupe_history_lines(self, raw_lines, ansi: bool) -> str:
        """Shared overlay-history cleaning pipeline — BOTH sources (tmux
        capture and pyte reconstruction) must pass through here so every
        dedup fix lands on both. Returns the joined text, each line closed
        with an SGR reset when ansi=True."""
        # Collapse terminal-resize redraw frames FIRST. When the window
        # is resized mid-stream (panel toggled, font changed, etc.) the
        # TUI re-renders the streaming block at the new width; the
        # scrollback keeps every wrap-variant. They share an identical
        # character stream but split at different points, so the
        # line-level dedup below can't see them. This wrap-invariant
        # pass drops the stale partial frames, keeping the last (current
        # width) render — Howard's "上滾看到同段落重複 N 次、樣式錯亂".
        raw_lines = self._collapse_redraw_frames(raw_lines)
        cleaned = []  # list of (stripped_for_compare, original_for_output)
        for line in raw_lines:
            # Strip bare CR — they survive tmux capture for some TUIs and
            # cause xterm.js (with convertEol: true) to jump to col 0 and
            # overwrite earlier chars, leaving only the line tail visible.
            # Replace with nothing; the `\n` split already handled row breaks.
            line = line.replace("\r", "")
            original = line.rstrip()
            stripped = self._ANSI_STRIP_RE.sub('', original).rstrip() if ansi else original
            if cleaned:
                prev_stripped, _ = cleaned[-1]
                # Current is strict prefix of previous → skip (rare)
                if prev_stripped.startswith(stripped) and stripped != prev_stripped:
                    continue
                # Previous is strict prefix of current → replace with longer
                if stripped.startswith(prev_stripped) and stripped != prev_stripped:
                    cleaned[-1] = (stripped, original)
                    continue
            cleaned.append((stripped, original))

        # Pass 2: collapse repeats. Two regimes that need different
        # gates because tmux scrollback mixes both kinds of duplicate:
        #
        # (a) Pure-CJK streaming redraw — Claude's reply rewrites the
        #     same 交付成果 / 敘事結構 block 5-10× while tokens stream;
        #     tmux records every frame.
        # (b) Mid-row redraw of a generic line — the same row gets
        #     re-emitted 3+ times in mixed CJK/ASCII content (tables
        #     where every cell is the same date label, audit reports
        #     where a single event line gets re-rendered after each
        #     status-bar refresh, etc.). Howard's screenshot:
        #     "Warren 寄 V1.5.1 部版資訊" appears 4× in a row.
        #
        # Two gates so we collapse both without nuking legit user
        # repeats (`return null;` appearing twice in code, two
        # adjacent table rows that genuinely share a date):
        #
        # Gate A: ≥ 90% CJK-cells AND ≥ MIN_WIDTH wide → dedup,
        #          keeping the LAST occurrence.
        # Gate B: any line wide enough to be distinctive AND seen
        #          ≥ 3 times in this capture → keep only the LAST.
        #          Threshold 3 (not 2) preserves natural-looking
        #          two-occurrence repeats.
        #
        # WHY keep LAST, not FIRST: in streaming/TUI-redraw contexts
        # the most recent rendering is the canonical one — earlier
        # instances are usually partial frames or context-mismatched
        # snapshots.  Classic failure: a user message captured during
        # the Claude Code splash banner (T1) and again after the real
        # reply starts streaming (T2).  Keep-first kept T1, so
        # scrolling up showed the user message followed by splash
        # banner content instead of the actual Claude reply.
        from collections import Counter
        DEDUP_MIN_WIDTH = 8
        REPEAT_GATE_MIN_WIDTH = 20      # raised from 12 — short
        REPEAT_GATE_THRESHOLD = 3        # numbered subtitles like
        # "3. Year One" (vw 12-14) and short Chinese sub-headings
        # like "雙語混搭" / "中文動詞句、有立場" used to land on this
        # gate when they appeared in three nearby outline blocks
        # ("英文短句、有電影感", "中文動詞句、有立場", repeated for
        # each section). Howard saw the "1." / "2." entries vanish
        # from his outline. Keep the gate tight to long redraw
        # strings (audit rows, full sentences); short headings now
        # always pass through.
        #
        # Dedup key is the ANSI-stripped line with whitespace runs
        # collapsed to a single space, so two captures that differ
        # only in indentation / residual escape bytes still compare
        # equal.
        def _key(s_stripped):
            return self._NORM_WHITESPACE_RE.sub(' ', s_stripped).strip()
        counts = Counter()
        for stripped, _ in cleaned:
            if self._visual_width(stripped.strip()) >= REPEAT_GATE_MIN_WIDTH:
                counts[_key(stripped)] += 1
        # Precompute the LAST index for each gated key so the final
        # loop can emit exactly one copy — the last (most canonical).
        last_idx_cjk = {}
        last_idx_rep = {}
        for i, (stripped, _) in enumerate(cleaned):
            k = _key(stripped)
            vw = self._visual_width(stripped.strip())
            is_cjk = (vw >= DEDUP_MIN_WIDTH
                      and self._cjk_cells(stripped.strip()) >= vw * 0.9)
            if is_cjk:
                last_idx_cjk[k] = i
            elif vw >= REPEAT_GATE_MIN_WIDTH and counts[k] >= REPEAT_GATE_THRESHOLD:
                last_idx_rep[k] = i
        final = []
        for i, (stripped, original) in enumerate(cleaned):
            k = _key(stripped)
            vw = self._visual_width(stripped.strip())
            is_cjk = (vw >= DEDUP_MIN_WIDTH
                      and self._cjk_cells(stripped.strip()) >= vw * 0.9)
            if is_cjk:
                if last_idx_cjk.get(k) != i:
                    continue
            elif vw >= REPEAT_GATE_MIN_WIDTH and counts[k] >= REPEAT_GATE_THRESHOLD:
                if last_idx_rep.get(k) != i:
                    continue
            final.append((stripped, original))
        # Append SGR reset to each line so an unclosed \x1b[...m on one
        # line can't bleed background/foreground colors into subsequent
        # lines when rendered in xterm.js (manifested as a giant red /
        # dark-bg rectangle across several rows in the overlay).
        reset = "\x1b[0m" if ansi else ""
        return "\n".join(orig + reset for _, orig in final)

    _TRANSCRIPT_TAIL_BYTES = 2 * 1024 * 1024

    _TRANSCRIPT_MAX_RECORDS = 3000

    def _transcript_history_response(self, s, sid: str, ansi: bool):
        """Overlay text rendered from the session's transcript JSONL, or
        None when this tab has no usable transcript (→ caller falls back to
        the terminal pipeline). Fidelity note: events come from
        agent_status's normalizer, which keeps user/assistant text and
        tool-call one-liners; interleaved thinking is omitted by design."""
        worker = {
            "cmd": getattr(s, "cmd", ""),
            "cwd": getattr(s, "cwd", "~"),
            "tmux_name": getattr(s, "_tmux_name", None),
            "session_id": getattr(s, "session_id", None),
        }
        kind = agent_status._worker_kind(worker["cmd"])
        if kind not in ("claude", "codex"):
            return None
        path = agent_status.resolve_transcript(worker)
        if not path or not os.path.exists(path):
            return None
        # Codex 的 resolve fallback 是「全域最新 rollout」——給狀態燈當近似
        # 可以，拿來渲染上滾對話不行（會把別的分頁的對話端給使用者）。只信
        # lsof 命中（該 pane 程序真的開著這個檔）的路徑。
        if kind == "codex":
            pane = agent_status._tmux_pane_pid(worker.get("tmux_name"))
            hit = (agent_status._lsof_open_jsonl(
                agent_status._pid_tree(pane), "/.codex/sessions/")
                if pane else None)
            if hit != path:
                return None
        fmt, evs, err = agent_status._read_tail_events(
            path, tail_bytes=self._TRANSCRIPT_TAIL_BYTES,
            max_records=self._TRANSCRIPT_MAX_RECORDS)
        if err or not evs:
            return None
        text = self._render_transcript_overlay(evs, ansi)
        plain = self._ANSI_STRIP_RE.sub('', text) if ansi else text
        # Sparse transcript (fresh tab, a lone /command) reads worse than
        # the terminal view — fall back below this floor.
        if len(plain.strip()) < 400 or plain.count("\n") < 8:
            return None
        return json.dumps({
            "success": True,
            "text": text,
            "ansi": ansi,
            "source": f"transcript ({fmt})",
        })

    @staticmethod
    def _render_transcript_overlay(evs, ansi: bool) -> str:
        """Normalized transcript events → terminal-styled conversation text.
        Styling mirrors the live TUI reading experience: user lines get a
        bold-cyan ❯ prefix, tool calls are dim one-liners, decision
        requests are yellow. Every styled line closes with SGR reset."""
        B_CYAN = "\x1b[1;36m" if ansi else ""
        DIM = "\x1b[2m" if ansi else ""
        YEL = "\x1b[33m" if ansi else ""
        RED = "\x1b[31m" if ansi else ""
        R = "\x1b[0m" if ansi else ""
        out = []
        for ev in evs:
            k = ev.get("kind")
            if k == "user_msg" and (ev.get("text") or "").strip():
                if out:
                    out.append("")
                for i, ln in enumerate(ev["text"].splitlines()):
                    out.append((f"{B_CYAN}❯ {R}" if i == 0 else "  ") + ln + R)
            elif k == "assistant_text" and (ev.get("text") or "").strip():
                if out:
                    out.append("")
                out.extend(ln + R for ln in ev["text"].splitlines())
            elif k == "tool_call":
                tool = ev.get("tool") or "?"
                tgt = ev.get("target") or ""
                out.append(f"{DIM}⏺ {tool}({tgt}){R}")
            elif k == "decision_req":
                out.append(f"{YEL}⚠ 等待決策 {ev.get('target', '')}{R}")
            elif k == "error" and ev.get("text"):
                out.append(f"{RED}✖ {ev['text']}{R}")
        return "\n".join(out)

    def history_audit(self, sid: str) -> str:
        """Self-check for "上滾看到不對的歷史" bugs.

        Compares four snapshots of the same session at the moment of
        capture and writes the full set to disk so a follow-up Claude
        invocation can reason about the discrepancy WITHOUT having to
        guess what the user sees:

          1. `last_extracted` — the AI reply we already sent to TG.
             Ground truth: this is the text the bridge believes the AI
             produced, derived from pyte's screen-diff extractor.
          2. `tmux_cleaned` — what `get_clean_history` returns to the
             scroll-up overlay (post-dedup).
          3. `tmux_raw` — pre-dedup tmux capture-pane, ansi-stripped.
             If something is missing from `tmux_cleaned` but present
             here, the dedup logic is at fault.
          4. `pyte_history` — pyte's own `history.top` rendered as text.
             Independent source — if `tmux_raw` and `pyte_history`
             disagree, the discrepancy points at tmux / pyte fidelity
             rather than dedup.

        Returns a JSON dict containing:
          - `summary`: counts + verdict + path to the on-disk dump
          - `missing_from_overlay`: reply lines that don't appear in the
            tmux_cleaned text (these are exactly the "上滾找不到 reply 上半段"
            cases Howard reported).
          - `noise_in_overlay`: overlay lines that don't appear in
            tmux_raw OR last_extracted — these are spurious entries the
            dedup pass left behind.
        """
        import re as _re

        s = self.sessions.get(sid)
        if not s:
            return json.dumps({"success": False, "message": f"no such session: {sid}"})

        # 1. last_extracted from bridge slot
        last_extracted = ""
        last_extracted_ts = 0.0
        if self.bridge is not None:
            try:
                slot = self.bridge.slots.get(sid)
            except Exception:
                slot = None
            if slot is not None:
                last_extracted = getattr(slot, "last_extracted_text", "") or ""
                last_extracted_ts = getattr(slot, "last_extraction_ts", 0.0) or 0.0

        # 2. tmux cleaned (what overlay shows)
        tmux_cleaned = ""
        tmux_cleaned_src = ""
        try:
            raw = self.get_clean_history(sid, max_lines=10000, ansi=False)
            r = json.loads(raw) if isinstance(raw, str) else raw
            if r.get("success"):
                tmux_cleaned = r.get("text", "") or ""
                tmux_cleaned_src = r.get("source", "")
        except Exception as e:
            tmux_cleaned_src = f"error: {e}"

        # 3. tmux raw — bypass dedup so we can isolate where bytes go missing
        tmux_raw = ""
        if getattr(s, "_tmux_name", None):
            try:
                r_raw = subprocess.run(
                    ["tmux", "capture-pane", "-p", "-J", "-t", s._tmux_name,
                     "-S", "-10000"],
                    capture_output=True, text=True, timeout=5,
                )
                if r_raw.returncode == 0:
                    tmux_raw = self._ANSI_STRIP_RE.sub('', r_raw.stdout)
            except Exception:
                _swallow("Api.history_audit:3813")

        # 4. pyte history (independent path)
        pyte_text = ""
        if self.bridge is not None:
            try:
                slot = self.bridge.slots.get(sid)
            except Exception:
                slot = None
            if slot is not None and getattr(slot, "screen", None) is not None:
                try:
                    pyte_text = self._pyte_history_text(slot)
                except Exception:
                    _swallow("Api.history_audit:3826")

        # Normalise for comparison — collapse whitespace runs, drop empty.
        def _norm_lines(text):
            out = []
            for line in (text or "").splitlines():
                norm = _re.sub(r"\s+", " ", line).strip()
                if norm:
                    out.append(norm)
            return out

        reply_lines = _norm_lines(last_extracted)
        overlay_lines = _norm_lines(tmux_cleaned)
        raw_lines = _norm_lines(tmux_raw)

        overlay_set = set(overlay_lines)
        raw_set = set(raw_lines)
        reply_set = set(reply_lines)

        # Missing: lines in last_extracted not present anywhere in overlay.
        # We accept partial containment (reply line is a substring of an
        # overlay line) so wrapped/reflowed rows don't false-flag.
        def _present_anywhere(line, bucket):
            if line in bucket:
                return True
            for cand in bucket:
                if line and (line in cand or cand in line) and len(line) > 6:
                    return True
            return False

        missing_from_overlay = [
            l for l in reply_lines
            if not _present_anywhere(l, overlay_set)
        ]
        # Noise: overlay lines that don't trace back to reply OR raw tmux
        # bytes. Anything not in raw_set is definitely not from the live
        # session — likely cross-tab bleed or stale capture state.
        # N/A for the transcript source: its text legitimately reaches
        # further back than tmux scrollback, so raw-containment would
        # false-flag old conversation as noise.
        if tmux_cleaned_src.startswith("transcript"):
            noise_in_overlay = []
        else:
            noise_in_overlay = [
                l for l in overlay_lines
                if not _present_anywhere(l, raw_set) and not _present_anywhere(l, reply_set)
            ]

        # Duplicates — the "上滾對話重複" bug class this audit previously
        # couldn't see (it only measured missing/noise). A normalized line
        # wide enough to be distinctive (visual width ≥ 20) should survive
        # the dedup pipeline at most twice (legit echoes); ≥3 occurrences in
        # the CLEANED overlay means a redraw frame slipped through.
        # Terminal sources only: transcript-rendered overlays legitimately
        # repeat tool one-liners（⏺ Edit(main.py) ×N＝真實事件）— live audit
        # on s94 false-flagged 8 of them the first time this shipped.
        if tmux_cleaned_src.startswith("transcript"):
            dup_in_overlay = []
        else:
            from collections import Counter as _Counter
            dup_counts = _Counter(
                l for l in overlay_lines if self._visual_width(l) >= 20)
            dup_in_overlay = [
                f"{n}× {l}" for l, n in dup_counts.most_common() if n >= 3]

        # Dump everything to disk so a later debugging pass can re-analyse
        # without re-running the user's session. Filename embeds sid + ts
        # so successive audits are kept side-by-side instead of overwriting.
        diag_dir = Path.home() / ".config" / "shellframe" / "diag"
        try:
            diag_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            _swallow("Api.history_audit:3886")
        ts_tag = time.strftime("%Y%m%d_%H%M%S", time.localtime())
        dump_path = diag_dir / f"history-audit_{sid}_{ts_tag}.txt"
        try:
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write(f"# history_audit  sid={sid}  ts={ts_tag}\n")
                f.write(f"# tmux_cleaned source: {tmux_cleaned_src}\n")
                f.write(f"# last_extracted_ts: {last_extracted_ts}\n")
                f.write(f"# counts: reply={len(reply_lines)} "
                        f"overlay={len(overlay_lines)} raw={len(raw_lines)} "
                        f"pyte_chars={len(pyte_text)}\n")
                f.write(f"# missing_from_overlay={len(missing_from_overlay)} "
                        f"noise_in_overlay={len(noise_in_overlay)}\n\n")
                f.write("===== LAST_EXTRACTED (reply ground-truth) =====\n")
                f.write(last_extracted + "\n\n")
                f.write("===== TMUX_CLEANED (overlay returns this) =====\n")
                f.write(tmux_cleaned + "\n\n")
                f.write("===== TMUX_RAW (pre-dedup) =====\n")
                f.write(tmux_raw + "\n\n")
                f.write("===== PYTE_HISTORY =====\n")
                f.write(pyte_text + "\n\n")
                f.write("===== MISSING_FROM_OVERLAY (reply lines not found in overlay) =====\n")
                for l in missing_from_overlay:
                    f.write("  - " + l + "\n")
                f.write("\n===== NOISE_IN_OVERLAY (overlay lines not in raw OR reply) =====\n")
                for l in noise_in_overlay:
                    f.write("  - " + l + "\n")
                f.write("\n===== DUP_IN_OVERLAY (≥3× wide lines that survived dedup) =====\n")
                for l in dup_in_overlay:
                    f.write("  - " + l + "\n")
        except Exception as e:
            return json.dumps({"success": False, "message": f"dump failed: {e}"})

        verdict_parts = []
        if missing_from_overlay:
            verdict_parts.append(
                f"BUG: {len(missing_from_overlay)} reply line(s) not found in "
                f"scroll-up overlay — '上滾找不到 reply' confirmed."
            )
        if noise_in_overlay:
            verdict_parts.append(
                f"BUG: {len(noise_in_overlay)} overlay line(s) match neither raw tmux "
                f"nor reply — spurious content surfacing."
            )
        if dup_in_overlay:
            verdict_parts.append(
                f"BUG: {len(dup_in_overlay)} distinct line(s) appear ≥3× in the "
                f"cleaned overlay — redraw duplicates slipped through dedup."
            )
        if not verdict_parts:
            if not last_extracted:
                verdict_parts.append(
                    "Inconclusive — no extracted reply on slot yet. "
                    "Send a message, wait for AI reply to land, then re-run."
                )
            else:
                verdict_parts.append("Overlay is consistent with reply + raw bytes.")

        return json.dumps({
            "success": True,
            "message": " ".join(verdict_parts),
            "details": {
                "dump_path": str(dump_path),
                "tmux_source": tmux_cleaned_src,
                "reply_lines": len(reply_lines),
                "overlay_lines": len(overlay_lines),
                "raw_lines": len(raw_lines),
                "pyte_chars": len(pyte_text),
                "missing_count": len(missing_from_overlay),
                "noise_count": len(noise_in_overlay),
                "dup_count": len(dup_in_overlay),
                "missing_sample": missing_from_overlay[:5],
                "noise_sample": noise_in_overlay[:5],
                "dup_sample": dup_in_overlay[:5],
            },
        })

    def _pyte_fallback_response(self, sid: str, ansi: bool = False) -> str:
        """Build a get_clean_history response from the bridge's pyte slot.
        Used when tmux capture isn't available."""
        if not self.bridge:
            return json.dumps({"success": False, "reason": "no tmux", "text": ""})
        try:
            slot = self.bridge.slots.get(sid)
        except Exception:
            slot = None
        if slot is None or getattr(slot, "screen", None) is None:
            return json.dumps({"success": False, "reason": "no tmux", "text": ""})
        try:
            text = self._pyte_history_text(slot, ansi=ansi)
        except Exception:
            text = ""
        if text:
            text = self._dedupe_history_lines(text.split("\n"), ansi)
        if text and text.strip():
            return json.dumps({
                "success": True, "text": text, "ansi": ansi,
                "source": "pyte (no-tmux)",
            })
        return json.dumps({"success": False, "reason": "no history", "text": ""})

    def enter_scroll_history(self, sid: str) -> str:
        """Enter tmux copy-mode for scrollable history.
        xterm.js scrollback is always empty for TUI apps (Claude/Codex) that
        use cursor-positioning instead of line-feeds. tmux's own pane buffer
        has the real scrollback. This triggers copy-mode + PageUp so the user
        sees old conversation. Press q to exit."""
        s = self.sessions.get(sid)
        if not s or not s._tmux_name:
            return json.dumps({"success": False, "reason": "no tmux"})
        try:
            subprocess.run(["tmux", "copy-mode", "-t", s._tmux_name],
                           capture_output=True, timeout=3)
            # Immediate PageUp so the user sees history right away
            subprocess.run(["tmux", "send-keys", "-t", s._tmux_name, "PageUp"],
                           capture_output=True, timeout=3)
            return json.dumps({"success": True})
        except Exception as e:
            return json.dumps({"success": False, "reason": str(e)})

    def scroll_history(self, sid: str, direction: str, lines: int = 3) -> str:
        """Scroll within tmux copy-mode. Enters copy-mode automatically if
        needed. On first entry parks the cursor at top-line so the very next
        scroll-up walks straight into scrollback (no wasted cursor motion
        across the visible rows). Auto-exits when scrolling reaches the live
        bottom — also forces cursor to bottom-line on exit so the live view
        is fully visible. Returns whether still in copy-mode after scrolling."""
        _dlog("scroll", f"sid={sid} direction={direction} lines={lines}")
        s = self.sessions.get(sid)
        if not s or not s._tmux_name:
            return json.dumps({"success": False, "inCopyMode": False})
        lines = max(1, min(lines, 15))
        t = s._tmux_name
        try:
            r = subprocess.run(
                ["tmux", "display-message", "-t", t, "-p", "#{pane_in_mode}"],
                capture_output=True, text=True, timeout=3)
            in_mode = r.stdout.strip() == "1"
            _dlog("scroll", f"  in_mode={in_mode}")

            if direction == "up":
                if not in_mode:
                    subprocess.run(["tmux", "copy-mode", "-t", t],
                                   capture_output=True, timeout=3)
                    # Park cursor at the very top of the visible area so the
                    # next cursor-up motion immediately scrolls into history
                    # rather than walking down→up across visible rows.
                    subprocess.run(
                        ["tmux", "send-keys", "-t", t, "-X", "top-line"],
                        capture_output=True, timeout=3)
                # Use semantic copy-mode command (works under both vi/emacs)
                for _ in range(lines):
                    subprocess.run(
                        ["tmux", "send-keys", "-t", t, "-X", "cursor-up"],
                        capture_output=True, timeout=3)
            elif direction == "down" and in_mode:
                # Park cursor at the bottom of the visible area first, so the
                # subsequent cursor-down keys actually scroll the SCREEN
                # (driving the scrollbar back toward live) instead of just
                # walking the cursor across visible rows.
                subprocess.run(
                    ["tmux", "send-keys", "-t", t, "-X", "bottom-line"],
                    capture_output=True, timeout=3)
                for _ in range(lines):
                    subprocess.run(
                        ["tmux", "send-keys", "-t", t, "-X", "cursor-down"],
                        capture_output=True, timeout=3)
                # Check scroll position — at bottom (0) → exit cleanly
                rp = subprocess.run(
                    ["tmux", "display-message", "-t", t, "-p", "#{scroll_position}"],
                    capture_output=True, text=True, timeout=3)
                try:
                    scroll_pos = int(rp.stdout.strip() or "0")
                except (ValueError, TypeError):
                    scroll_pos = 0
                if scroll_pos == 0:
                    subprocess.run(
                        ["tmux", "send-keys", "-t", t, "-X", "cancel"],
                        capture_output=True, timeout=3)

            r2 = subprocess.run(
                ["tmux", "display-message", "-t", t, "-p", "#{pane_in_mode}"],
                capture_output=True, text=True, timeout=3)
            still_in = r2.stdout.strip() == "1"
            _dlog("scroll", f"  done sid={sid} still_in_copy_mode={still_in}")
            return json.dumps({"success": True, "inCopyMode": still_in})
        except Exception as e:
            _dlog("scroll", f"  ERROR sid={sid} {e}")
            return json.dumps({"success": False, "inCopyMode": False, "reason": str(e)})
