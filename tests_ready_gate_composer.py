"""回歸：輸入框裡有字（灰色建議字／沒送出的草稿）不等於停在選單。

回報：TG 發的訊息都進不來。量到的原因：Claude Code 會在閒置的輸入列放一段
dim 灰字「建議下一句」（`❯`＋不換行空白＋\\x1b[2m…），使用者也可能留著草稿。
去掉 ANSI 之後那一列是「❯ 某段字」，空輸入列配不到 → 改比選單形狀 → 被捲動區
「❯ 上一則訊息＋縮排的 ⎿ 附件列」或草稿第二行配中 → 判成「等你選」。TG 第一則
訊息因此被丟掉，還被說成「停在信任對話框」並附上沒有作用的信任按鈕。
實測當下 22 個分頁有 3 個這樣卡著。

跑法：.venv/bin/python tests_ready_gate_composer.py
"""

import inspect

import bridge_telegram
import main

A = main.Api
B = "─" * 60
FAILED = []


def check(name, cond):
    print(("  ok   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED.append(name)


def verdict(raw):
    """startup_dialog_blocking 的判斷順序，餵畫面文字。"""
    clean = A._ANSI_RE.sub('', A._blank_composer_line(raw))
    if A._STARTUP_TRUST_RE.search(clean):
        return "trust"
    if A._STARTUP_EXIT_OPTION_RE.search(clean):
        return "exit-menu"
    m = None if A._COMPOSER_RE.search(clean) else A._MENU_RE.search(clean)
    return "menu" if m else "ok"


def old_verdict(raw):
    clean = A._ANSI_RE.sub('', raw)
    m = None if A._COMPOSER_RE.search(clean) else A._MENU_RE.search(clean)
    return "menu" if m else "ok"


# 實際擷取的形狀：捲動區有一則帶附件的舊訊息，輸入框裡是灰色建議字。
GHOST = (
    "❯ 這個案例幫我補上 小N那塊的圖片案例   [Image #5]  [Image #6]\n"
    "  ⎿  [Image #4]\n"
    "\n"
    "⏺ 已補上。\n"
    "\n"
    f"\x1b[38;5;244m{B}\n"
    "\x1b[39m❯ \x1b[2m幕僚長走的是 Grok Bot 桌面 app 在 Mac 上跑指令\x1b[0m\n"
    f"\x1b[38;5;244m{B}\n"
    "\x1b[39m  \x1b[38;5;211m⏵⏵ bypass permissions on\x1b[38;5;246m (shift+tab to cycle)\n"
)

# 實際擷取的形狀：捲動區有一則舊提問（下面接縮排的工具列），輸入框裡是兩行、
# 沒送出的一般顏色草稿。
DRAFT = (
    "❯ 幫我追加一下 凱基期貨 的議題討論 0.5\n"
    "\n"
    "  Ran 2 shell commands\n"
    "\n"
    "✻ Baked for 12s · done 10:03 AM\n"
    f"{B}\n"
    "❯ 今天幫我補排一張臨時的卡片是新光銀行有上版狀況排除 1.5小時\n"
    "\n"
    "  還有幫我排\n"
    f"{B}\n"
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
)

TRUST = (
    f"{B}\n"
    " Do you trust the files in this folder?\n"
    "\n"
    " ❯ 1. Yes, I trust this folder\n"
    "   2. No, exit\n"
    "\n"
    " Enter to confirm · Esc to cancel\n"
)

CREDITS = (
    "Fable 5.1 now uses usage credits\n\nYou don't have usage credits yet.\n\n"
    "❯ Switch to Sonnet 5 and continue\n"
    "  Set up usage credits on claude.ai\n"
)

# 選單緊貼在框線下、底下沒有收尾框線：不是輸入框，必須照樣擋。
MENU_UNDER_RULE = f"{B}\n❯ Enable it now\n  Ask me later\n\n Enter to confirm\n"


def main_():
    check("灰色建議字：修正前會被誤判成選單（重現回報）", old_verdict(GHOST) == "menu")
    check("灰色建議字：現在判為可以送", verdict(GHOST) == "ok")
    check("沒送出的草稿：修正前會被誤判成選單", old_verdict(DRAFT) == "menu")
    check("沒送出的草稿：現在判為可以送", verdict(DRAFT) == "ok")
    check("信任對話框照樣擋下", verdict(TRUST) == "trust")
    check("額度選單照樣擋下", verdict(CREDITS) == "menu")
    check("緊貼框線、沒有下框線的選單照樣擋下", verdict(MENU_UNDER_RULE) == "menu")
    check("只換掉輸入框那一列，其餘畫面原封不動",
          A._blank_composer_line(GHOST).count("\n") == GHOST.count("\n")
          and "[Image #5]" in A._blank_composer_line(GHOST))

    src = inspect.getsource(A.startup_dialog_blocking)
    check("startup_dialog_blocking 在比對前先處理輸入框",
          "_blank_composer_line(" in src
          and src.find("_blank_composer_line(") < src.find("_COMPOSER_RE.search"))

    # TG：只有真的信任對話框才給「信任」按鈕
    sent, offered = [], []
    b = object.__new__(bridge_telegram.TelegramBridge)
    b.config = type("C", (), {"bot_token": "x"})()
    b._offer_trust_buttons = lambda *a, **k: offered.append(a) or True
    slot = type("S", (), {"sid": "s1", "label": "測試分頁"})()
    saved = bridge_telegram.tg_api
    try:
        bridge_telegram.tg_api = lambda token, method, data=None, timeout=35: sent.append(data) or {}
        b._notify_input_blocked(slot, 1, "等你選：A ／ B")
        check("一般選單不給信任按鈕", not offered)
        check("一般選單照實說卡在什麼",
              sent and "等你選：A ／ B" in sent[-1]["text"] and "信任" not in sent[-1]["text"])
        sent.clear()
        b._notify_input_blocked(slot, 1, "啟動信任對話框")
        check("信任對話框才給按鈕", len(offered) == 1)
    finally:
        bridge_telegram.tg_api = saved

    print()
    if FAILED:
        print(f"{len(FAILED)} failed")
        raise SystemExit(1)
    print("ALL PASS")


if __name__ == "__main__":
    main_()
