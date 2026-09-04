"""Api mixin — scroll-history overlay domain（God-class 分批拆解 第一批）.

上滾歷史的完整鏈路：transcript 來源（AI 分頁 source of truth）→ pyte
alt-screen 重建 → tmux capture，共用 _dedupe_history_lines 去重管線；
含 history_audit 自檢與 pyte SGR 樣式重建。行為與 main.py 內時期
byte-identical，僅搬家。回歸測試：tests_history_dedup.py。
"""

import json
import os
import re
import sqlite3
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

    # tmux status-bar 行：`[sf_s69] 0:opencode.exe* [0,0] "OC | …" 10:44 05-Jul`
    # 同 bridge_telegram 的 noise 樣式，overlay 兩個來源共用的過濾。
    _TMUX_STATUS_RE = re.compile(r'^\s*\[sf_[^\]]+\]\s+\d+:\S+.*\[\d+,\d+\]')

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
        # opens to a wall of empty space — the user saw "上面不見了 / 整段
        #空白才出現條目". Internal blank lines (between paragraphs) are
        # preserved; only the top contiguous run is dropped.
        while out and not out[0][0].strip():
            out.pop(0)
        return '\n'.join(rendered for _, rendered in out)

    # 表格／框線用的字元（含 ASCII 的 +-| 與全套 box-drawing）。整行只由這些
    # 組成就是框線，不是內容。
    _BOX_RULE_RE = re.compile(r'[\s\u2500-\u257f|+=_-]+')

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
                # the banner (使用者: 上滾只剩 banner、沒對話). Only collapse
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

    def get_clean_history(self, sid: str, max_lines: int = 10000,
                          ansi: bool = True, cols: int = 0) -> str:
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
        prompt / unrelated history — the user's exact complaint.

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
            # for this session; failing that, the transcript.
            resp = self._pyte_fallback_response(sid, ansi=ansi)
            try:
                if not json.loads(resp).get("success"):
                    t = self._transcript_history_response(s, sid, ansi, cols) if s else None
                    if t:
                        return t
            except Exception:
                _swallow(f"get_clean_history.transcript:{sid}")
            return resp

        # Source order (v0.23.2, 實測後定調)：終端來源優先——那才是
        # 「跟活畫面同一個樣子」（tmux -e 原樣 SGR / pyte 重建），重複問題已由
        # 共用去重管線處理。transcript 渲染（markdown 近似＋工具行）跟 TUI
        # 讀感差距大（v0.23.0 把它放第一位，回報：「越差越多」），降級為
        # fallback——只在 pyte 拿不出東西時救場（典型：app 剛重啟、bridge 的
        # pyte 從零開始，而 alt-screen 下 tmux scrollback 又是錯的 buffer）。

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
            # OpenCode 例外：其 TUI 原地重繪，pyte history 只有目前一屏
            #（實測 25 行/1016 chars），terminal-first 會讓上滾「只剩一屏」。
            # transcript（opencode.db）才有完整對話 → 對 opencode 分頁反轉
            # 順序。claude/codex 維持 v0.23.2 terminal-first 不動。
            if self._is_opencode_cmd(getattr(s, "cmd", "")):
                try:
                    resp = self._transcript_history_response(s, sid, ansi, cols)
                    if resp:
                        return resp
                except Exception:
                    _swallow(f"get_clean_history.opencode:{sid}")
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
                # pyte empty/too-short（app 剛重啟、bridge 沒跑）→ 先試
                # transcript：alt-screen 下 tmux scrollback 是錯的 buffer
                #（normal-screen 舊內容），transcript 反而是唯一正確來源。
            try:
                resp = self._transcript_history_response(s, sid, ansi, cols)
                if resp:
                    return resp
            except Exception:
                _swallow(f"get_clean_history.transcript:{sid}")
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
        # width) render — the user's "上滾看到同段落重複 N 次、樣式錯亂".
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
            # tmux status bar（綠條）不是對話內容——pyte/capture 都可能把它
            # 收進 history（history-audit 對 s69 抓到 '[sf_s69] 0:opencode.exe*
            # [0,0] "OC | …"' 直出 overlay）。活畫面的綠條由 live terminal 自己
            # 顯示，overlay 一律濾掉。
            if self._TMUX_STATUS_RE.search(stripped):
                continue
            # 空行不能參與 strict-prefix 比較。空字串是任何字串的前綴，所以
            # 段落之間的空行會兩邊都中：current 是空 → 被判成「prev 的前綴」
            # 直接 skip；prev 是空 → 被判成「current 的前綴」而拿 current 蓋掉
            # 那個空行。結果**每一個內部空行都消失**，上滾看到的歷史整段黏成
            # 一團，跟活畫面的排版完全不同（Howard 2026-09-03：「上滑樣式會跑
            # 掉…我希望的就是無縫銜接」）。
            # 上面 _render_rows 的註解本來就寫明「Internal blank lines
            # (between paragraphs) are preserved」——這裡是實作跟設計意圖打架。
            if cleaned and stripped:
                prev_stripped, _ = cleaned[-1]
                if prev_stripped:
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
        #     status-bar refresh, etc.). the user's screenshot:
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
        # each section). the user saw the "1." / "2." entries vanish
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

        # 表格框線一律不參與重複摺疊。一張 13 列的表有 12 條一模一樣的
        # `├───┼───┼───┤`，Gate B（同一行出現 ≥3 次只留最後一份）會把它們
        # 全部吃掉，上滾看到的表格就整團黏在一起、只剩頭尾兩條線
        # （日常使用中回報，附上滑前後對照圖）。這跟空行是同一類：**合法的
        # 重複**，不是串流重繪的殘影。
        def _is_box_rule(s_stripped):
            t = s_stripped.strip()
            return bool(t) and not self._BOX_RULE_RE.sub('', t)
        counts = Counter()
        for stripped, _ in cleaned:
            if _is_box_rule(stripped):
                continue
            if self._visual_width(stripped.strip()) >= REPEAT_GATE_MIN_WIDTH:
                counts[_key(stripped)] += 1
        # Precompute the LAST index for each gated key so the final
        # loop can emit exactly one copy — the last (most canonical).
        last_idx_cjk = {}
        last_idx_rep = {}
        for i, (stripped, _) in enumerate(cleaned):
            if _is_box_rule(stripped):
                continue
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

    def _transcript_history_response(self, s, sid: str, ansi: bool, cols: int = 0):
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
            # OpenCode 分頁：TUI 原地重繪（Bubble Tea 式），捲出視窗的內容從
            # 不進 terminal scrollback / pyte history —— transcript（其 SQLite
            # session 庫）是唯一有完整對話的來源。
            if self._is_opencode_cmd(worker["cmd"]):
                return self._opencode_history_response(worker, ansi, cols)
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
        text = self._render_transcript_overlay(evs, ansi, cols=cols)
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

    # ── OpenCode transcript（SQLite）──
    # opencode（SST，開源模型 harness）把對話存在
    # ~/.local/share/opencode/opencode.db：session(title) → message(data:
    # {role,...}) → part(data: {type:text|reasoning|tool|step-*}).
    # session↔pane 的對應靠 pane title：opencode 會把 tmux pane title 設成
    # "OC | <session.title>"，直接反查 title 即可（多個 opencode 分頁也各自
    # 對到自己的 session）。

    _OPENCODE_DB = Path.home() / ".local" / "share" / "opencode" / "opencode.db"

    @staticmethod
    def _is_opencode_cmd(cmd: str) -> bool:
        for tok in (cmd or "").split():
            base = tok.split("/")[-1]
            if "." in base:
                base = base.rsplit(".", 1)[0]
            if base == "opencode":
                return True
        return False

    def _opencode_history_response(self, worker: dict, ansi: bool, cols: int = 0):
        """Overlay text from opencode's SQLite session store, or None
        （→ caller 落回 terminal 管線）. 事件 normalize 成與 claude/codex
        相同的形狀後走同一個 _render_transcript_overlay —— 樣式即與一般
        分頁一致。"""
        try:
            evs = self._opencode_events(worker)
        except Exception:
            _swallow("Api._opencode_history_response")
            return None
        if not evs:
            return None
        # claude/codex 有 sparse floor（太短的 transcript 讀感不如活畫面），
        # opencode 不能照抄：它的 fallback 就是活畫面本身。opencode 的 TUI
        # 原地重繪，捲出視窗的內容不進 terminal scrollback 也不進 pyte
        # history——alt-screen 下 tmux capture 拿到的就是使用者眼前那一屏，
        # 落回去等於「上滑看不到歷史」（日常使用回報）。所以門檻改成
        # 「transcript 裡有沒有真的對話」：只要有一則 user/assistant 訊息，
        # 它就是唯一有歷史的來源，短也要用。
        if not any(e.get("kind") in ("user_msg", "assistant_text") for e in evs):
            return None
        text = self._render_transcript_overlay(
            evs, ansi, self._SKIN_OPENCODE, cols)
        plain = self._ANSI_STRIP_RE.sub('', text) if ansi else text
        if not plain.strip():
            return None
        return json.dumps({
            "success": True,
            "text": text,
            "ansi": ansi,
            "source": "transcript (opencode)",
        })

    def _opencode_events(self, worker: dict, max_messages: int = 300):
        """讀 opencode.db，回傳 normalized 事件（同 agent_status._norm_* 形狀）。"""
        db = self._OPENCODE_DB
        if not db.exists():
            return []
        # session 對應（pane title → 同 cwd 最近一個）由 agent_status 提供，
        # 狀態燈與這裡共用同一份——兩份「這個分頁是哪個 session」必然會走鐘。
        ses_id = agent_status.opencode_session_id(worker, db_path=str(db))
        if not ses_id:
            return []
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=1.0)
        try:
            con.row_factory = sqlite3.Row
            # 只取最近 max_messages 則訊息（長 session 防整表掃）。
            rows = con.execute(
                "SELECT m.id AS mid, m.data AS mdata, m.time_created AS mts,"
                "       p.data AS pdata "
                "FROM (SELECT id, data, time_created FROM message "
                "      WHERE session_id = ? "
                "      ORDER BY time_created DESC, id DESC LIMIT ?) m "
                "JOIN part p ON p.message_id = m.id "
                "ORDER BY m.time_created ASC, m.id ASC, p.id ASC",
                (ses_id, max_messages)).fetchall()
        finally:
            con.close()

        evs = []
        cur_mid = None
        cur_role = ""
        cur_ts = None
        seen_texts = set()   # 同一則訊息內重複的 text part（實測會出現）只收一次

        def _ev_text(role, ts, text):
            text = (text or "").strip()
            if not text:
                return None
            kind = "user_msg" if role == "user" else "assistant_text"
            return {"kind": kind, "ts": ts, "text": text}

        for row in rows:
            if row["mid"] != cur_mid:
                cur_mid = row["mid"]
                seen_texts = set()
                try:
                    md = json.loads(row["mdata"])
                except Exception:
                    md = {}
                cur_role = md.get("role") or ""
                cur_ts = (row["mts"] or 0) / 1000.0 or None
            try:
                pd = json.loads(row["pdata"])
            except Exception:
                continue
            pt = pd.get("type")
            if pt == "text":
                t = (pd.get("text") or "").strip()
                if not t or t in seen_texts:
                    continue
                seen_texts.add(t)
                ev = _ev_text(cur_role, cur_ts, t)
                if ev:
                    evs.append(ev)
            elif pt == "tool":
                inp = (pd.get("state") or {}).get("input") or {}
                target = ""
                for k in ("filePath", "url", "command", "pattern", "path"):
                    v = inp.get(k)
                    if isinstance(v, str) and v:
                        target = v
                        break
                if not target:
                    for v in inp.values():
                        if isinstance(v, str) and v:
                            target = v
                            break
                evs.append({"kind": "tool_call", "ts": cur_ts,
                            "tool": pd.get("tool") or "?",
                            "target": target[:60]})
            elif pt == "step-finish":
                evs.append({"kind": "turn_end", "ts": cur_ts})
            # reasoning / step-start / 其他 → TUI 不顯示，overlay 也不顯示
        return evs

    # Harness 內部訊息（task-notification / system-reminder）以 user 角色寫進
    # transcript，但活畫面的 TUI 從不原樣顯示它們——overlay 也不該（使用者
    # 的「scroll 樣式不同」截圖裡整段 <usage>…</task-notification> 直出）。
    _TASK_NOTIF_RE = re.compile(r"<task-notification>.*?</task-notification>", re.S)
    _TASK_NOTIF_SUMMARY_RE = re.compile(r"<summary>(.*?)</summary>", re.S)
    _SYS_REMINDER_RE = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)

    @classmethod
    def _strip_harness_noise(cls, text: str, dim: str, reset: str) -> str:
        """User-role 事件裡的 harness 雜訊 → 摺疊成一行 dim 摘要或移除。"""
        def _notif(m):
            sm = cls._TASK_NOTIF_SUMMARY_RE.search(m.group(0))
            label = sm.group(1).strip() if sm else "背景任務通知"
            return f"{dim}⏺ {label}（內容略）{reset}"
        text = cls._TASK_NOTIF_RE.sub(_notif, text)
        text = cls._SYS_REMINDER_RE.sub("", text)
        return text.strip()

    # Markdown → ANSI：讓 overlay 的 assistant 文字接近活畫面 TUI 的渲染
    # （標題、粗體、行內 code、列點、引用、圍欄 code、表格），而不是把 `**`
    # 與反引號原樣露出。目標是「讀起來是同一個 app」。
    #
    # skin＝各家 TUI 的排版與配色。不同 harness 差很多，用同一套近似值就會出現
    # 「上滑跟活畫面對不上」的斷差感。opencode 的值是 2026-09-05 用
    # `tmux capture-pane -e` 從實機畫面量的，不是估的：內容縮排 5 欄、右緣留 2、
    # 列點保留 `-`、表格畫成方框並撐滿可用寬度、水平線與表格同寬、24-bit 原色。
    _MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
    _MD_CODE_RE = re.compile(r"`([^`\n]+)`")
    _MD_HDR_RE = re.compile(r"^(#{1,6})\s+(.*)$")
    _MD_BULLET_RE = re.compile(r"^(\s*)([-*])\s+")
    _MD_NUM_RE = re.compile(r"^(\s*)(\d{1,3}\.)\s+")
    _MD_HR_RE = re.compile(r"^\s*(---+|\*\*\*+)\s*$")
    _ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")
    # 表格分隔列：`|---|:--:|`。只在「上一列有 |」時才拿來判斷，所以不會跟
    # 水平線 `---` 搶。
    _MD_TABLE_SEP_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")

    _SKIN_DEFAULT = {
        "indent": 0, "right_margin": 0, "wrap": False,
        "user_prefix": "\x1b[1;36m❯ \x1b[0m", "user_cont": "  ",
        "text": "", "head": "\x1b[1m\x1b[36m", "bold": "\x1b[1m",
        "code": "\x1b[38;5;180m", "bullet": "\x1b[36m", "border": "\x1b[2m",
        "quote": "\x1b[2m", "fence": "\x1b[38;5;250m", "dim": "\x1b[2m",
        "bullet_marker": "•", "table_stretch": False, "table_pad": 1,
        "table_row_sep": False,
    }
    _SKIN_OPENCODE = {
        "indent": 5, "right_margin": 2, "wrap": True,
        "user_prefix": "  \x1b[38;2;92;156;245m┃\x1b[0m  ",
        "user_cont": "  \x1b[38;2;92;156;245m┃\x1b[0m  ",
        "user_pad": "  \x1b[38;2;92;156;245m┃\x1b[0m",
        "text": "\x1b[38;2;238;238;238m",
        "head": "\x1b[1m\x1b[38;2;157;124;216m",
        "bold": "\x1b[1m\x1b[38;2;245;167;66m",
        "code": "\x1b[38;2;127;216;143m",
        "bullet": "\x1b[38;2;250;178;131m",
        "border": "\x1b[38;2;128;128;128m",
        "quote": "\x1b[38;2;128;128;128m",
        "fence": "\x1b[38;2;170;170;170m",
        "dim": "\x1b[38;2;128;128;128m",
        "bullet_marker": None, "table_stretch": True, "table_pad": 0,
        "table_row_sep": True,
    }

    @classmethod
    def _disp_width(cls, s: str) -> int:
        """終端顯示寬度（全形算 2、組合字元算 0），ANSI 先剝掉。"""
        s = cls._ANSI_SGR_RE.sub("", s)
        w = 0
        for ch in s:
            if unicodedata.combining(ch):
                continue
            w += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
        return w

    # 中日文可以斷行的位置。實測 opencode 是「斷在標點之後」而不是任意全形字之後
    # ——同一段文字，斷在任意字後會多塞 4 欄、句子被切在詞中間，跟活畫面對不上。
    _CJK_BREAK_AFTER = set("、，。！？；：）」』】》〉…～・,.!?;:)]}")

    @classmethod
    def _wrap_ansi(cls, line: str, width: int):
        """依顯示寬度斷行，續行沿用當下的 SGR 狀態。

        續行的還原方式＝重放「自上一次 reset 起的所有 SGR 序列」。SGR 是狀態機，
        重放等價於還原當下狀態，也不必自己維護一份 attribute 表。

        斷點優先序：空白或標點之後（詞邊界）→ 任意全形字之後 → 硬切。
        """
        if width <= 0 or cls._disp_width(line) <= width:
            return [line]
        out, cur, w, sgr = [], [], 0, ""
        brk_word = brk_any = -1
        i, n = 0, len(line)
        while i < n:
            m = cls._ANSI_SGR_RE.match(line, i)
            if m:
                seq = m.group(0)
                cur.append(seq)
                sgr = "" if seq == "\x1b[0m" else sgr + seq
                i = m.end()
                continue
            ch = line[i]
            cw = 0 if unicodedata.combining(ch) else (
                2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1)
            if w + cw > width and cur:
                brk = brk_word if brk_word > 0 else brk_any
                head, tail = (cur[:brk], cur[brk:]) if brk > 0 else (cur, [])
                out.append("".join(head).rstrip())
                cur = ([sgr] if sgr else []) + tail
                w = cls._disp_width("".join(tail))
                brk_word = brk_any = -1
            cur.append(ch)
            w += cw
            if ch == " " or ch in cls._CJK_BREAK_AFTER:
                brk_word = len(cur)
            if cw == 2:
                brk_any = len(cur)
            i += 1
        if cur:
            out.append("".join(cur).rstrip())
        return out

    @classmethod
    def _md_table_at(cls, lines, i):
        """lines[i] 起是不是 markdown 表格？回 (rows, 下一個 index) 或 None。"""
        if i + 1 >= len(lines) or "|" not in lines[i]:
            return None
        if not cls._MD_TABLE_SEP_RE.match(lines[i + 1]) or "|" not in lines[i + 1]:
            return None

        def cells(ln):
            s = ln.strip()
            if s.startswith("|"):
                s = s[1:]
            if s.endswith("|"):
                s = s[:-1]
            return [c.strip() for c in s.split("|")]

        rows = [cells(lines[i])]
        j = i + 2
        while (j < len(lines) and "|" in lines[j]
               and not cls._MD_TABLE_SEP_RE.match(lines[j])):
            rows.append(cells(lines[j]))
            j += 1
        return rows, j

    @classmethod
    def _render_md_table(cls, rows, skin, width, ansi):
        """markdown 表格 → 方框表。

        撐滿模式（opencode）的欄寬公式是實機反推出來的：每欄先取內容自然寬，
        再把剩餘寬度平分，餘數由左往右各加 1。兩組實測都對得上
        （3 欄 6/6/6 → 28/27/27；2 欄 14/18 → 40/43）。
        """
        n = max(len(r) for r in rows)
        rows = [r + [""] * (n - len(r)) for r in rows]
        pad = skin["table_pad"]
        nat = [max(cls._disp_width(r[c]) for r in rows) + pad * 2 for c in range(n)]
        if skin["table_stretch"] and width > 0:
            inner = max(sum(nat), width - (n + 1))
            add, rem = divmod(inner - sum(nat), n)
            w = [nat[c] + add + (1 if c < rem else 0) for c in range(n)]
        else:
            w = nat
        B = skin["border"] if ansi else ""
        HEAD = skin["head"] if ansi else ""
        TEXT = skin["text"] if ansi else ""
        R = "\x1b[0m" if ansi else ""

        def rule(left, mid, right):
            return B + left + mid.join("─" * x for x in w) + right + R

        def row(cells, head=False):
            col = HEAD if head else TEXT
            parts = []
            for c, cell in enumerate(cells):
                body = " " * pad + cell
                body += " " * max(0, w[c] - cls._disp_width(body))
                parts.append(f"{col}{body}{R}" if col else body)
            return B + "│" + R + (B + "│" + R).join(parts) + B + "│" + R

        out = [rule("┌", "┬", "┐"), row(rows[0], head=True), rule("├", "┼", "┤")]
        for idx, r in enumerate(rows[1:]):
            if idx and skin["table_row_sep"]:
                out.append(rule("├", "┼", "┤"))
            out.append(row(r))
        out.append(rule("└", "┴", "┘"))
        return out

    @classmethod
    def _md_ansi_lines(cls, text: str, ansi: bool, skin=None, width: int = 0):
        if not ansi:
            return text.splitlines()
        skin = skin or cls._SKIN_DEFAULT
        R = "\x1b[0m"
        TEXT, HEAD, BOLD = skin["text"], skin["head"], skin["bold"]
        CODE, BULLET, DIM = skin["code"], skin["bullet"], skin["dim"]
        src = text.splitlines()
        out = []
        in_fence = False
        i = 0
        while i < len(src):
            ln = src[i]
            if ln.lstrip().startswith("```"):
                in_fence = not in_fence
                out.append(f"{DIM}{ln}{R}")
                i += 1
                continue
            if in_fence:
                out.append(f"{skin['fence']}{ln}{R}")
                i += 1
                continue
            tbl = cls._md_table_at(src, i)
            if tbl:
                rows, i = tbl
                out.extend(cls._render_md_table(rows, skin, width, ansi))
                continue
            i += 1
            m = cls._MD_HDR_RE.match(ln)
            if m:
                out.append(f"{HEAD}{m.group(2)}{R}")
                continue
            if cls._MD_HR_RE.match(ln):
                out.append(f"{DIM}{'─' * (width if width > 0 else 40)}{R}")
                continue
            if ln.lstrip().startswith(">"):
                out.append(f"{skin['quote']}{ln}{R}")
                continue
            marker = skin["bullet_marker"]
            if marker:
                ln = cls._MD_BULLET_RE.sub(f"\\1{BULLET}{marker}{R}{TEXT} ", ln, count=1)
            else:
                ln = cls._MD_BULLET_RE.sub(f"\\1{BULLET}\\2{R}{TEXT} ", ln, count=1)
            ln = cls._MD_NUM_RE.sub(f"\\1{BULLET}\\2{R}{TEXT} ", ln, count=1)
            ln = cls._MD_BOLD_RE.sub(f"{BOLD}\\1{R}{TEXT}", ln)
            ln = cls._MD_CODE_RE.sub(f"{CODE}\\1{R}{TEXT}", ln)
            out.append(TEXT + ln if TEXT else ln)
        if skin["wrap"]:
            wrapped = []
            for ln in out:
                wrapped.extend(cls._wrap_ansi(ln, width))
            out = wrapped
        return out

    @classmethod
    def _render_transcript_overlay(cls, evs, ansi: bool, skin=None,
                                   cols: int = 0) -> str:
        """Normalized transcript events → terminal-styled conversation text.

        Styling follows the tab's skin so the overlay reads like that harness's
        live TUI: the user marker, indent, palette and table style are the ones
        measured from the real app. Harness noise is collapsed, tool calls are
        dim one-liners, decision requests are yellow. Every styled line closes
        with an SGR reset. `cols` is the live pane width — the renderer needs it
        to size tables and rules the way the TUI does; 0 means unknown, and
        everything falls back to unwrapped, content-sized output."""
        skin = skin or cls._SKIN_DEFAULT
        indent = " " * skin["indent"]
        width = max(0, cols - skin["indent"] - skin["right_margin"]) if cols else 0
        DIM = skin["dim"] if ansi else ""
        YEL = "\x1b[33m" if ansi else ""
        RED = "\x1b[31m" if ansi else ""
        R = "\x1b[0m" if ansi else ""
        u_first = skin["user_prefix"] if ansi else "❯ "
        u_cont = skin["user_cont"] if ansi else "  "
        u_width = (max(0, cols - cls._disp_width(u_first) - skin["right_margin"])
                   if cols and skin["wrap"] else 0)
        out = []
        # 連續 tool_call 收合成一行摘要——一次修 bug 動輒 20+ 個工具呼叫，
        # 逐行列出是工具行牆（使用者 截圖的主要噪音），TUI 讀感也不是那樣。
        pending_tools = []

        def _flush_tools():
            if not pending_tools:
                return
            if len(pending_tools) <= 2:
                for tool, tgt in pending_tools:
                    out.append(f"{indent}{DIM}⏺ {tool}({tgt}){R}")
            else:
                from collections import Counter
                counts = Counter(t for t, _ in pending_tools)
                summary = "、".join(
                    f"{t} ×{n}" if n > 1 else t for t, n in counts.most_common())
                out.append(f"{indent}{DIM}⏺ {summary}{R}")
            pending_tools.clear()

        for ev in evs:
            k = ev.get("kind")
            if k == "tool_call":
                pending_tools.append((ev.get("tool") or "?", ev.get("target") or ""))
                continue
            if k in ("tool_result", "turn_end"):
                continue  # 不 flush：讓跨 result 的連續工具呼叫也能收合
            _flush_tools()
            if k == "user_msg" and (ev.get("text") or "").strip():
                text = cls._strip_harness_noise(ev["text"], DIM, R)
                text = re.sub(r"\[Image[^\]]*\]", "📎 圖片", text)
                if not text:
                    continue
                if out:
                    out.append("")
                body = []
                for ln in text.splitlines():
                    body.extend(cls._wrap_ansi(ln, u_width) if u_width else [ln])
                pad = skin.get("user_pad") or ""
                if pad:
                    out.append(pad)
                for i, ln in enumerate(body):
                    out.append((u_first if i == 0 else u_cont) + ln + R)
                if pad:
                    out.append(pad)
            elif k == "assistant_text" and (ev.get("text") or "").strip():
                if out:
                    out.append("")
                # 空行不要帶縮排：活畫面的空行就是空的，留一排空白會讓
                # 「複製上滑內容」多出看不見的尾隨空格。
                out.extend(
                    (indent + ln + R) if cls._ANSI_STRIP_RE.sub('', ln).strip() else ""
                    for ln in cls._md_ansi_lines(ev["text"], ansi, skin, width))
            elif k == "decision_req":
                out.append(f"{indent}{YEL}⚠ 等待決策 {ev.get('target', '')}{R}")
            elif k == "error" and ev.get("text"):
                out.append(f"{indent}{RED}✖ {ev['text']}{R}")
        _flush_tools()
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
            cases reported).
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
