"""Messages that went into a Claude Code tab as a paste, in the shapes measured on a tab in daily
use (47 user turns). The words are demo words; only the shapes come from that tab.

Claude Code keeps every paste in its record wrapped: <pasted_content id="…">…</pasted_content id="…">,
the id four hex digits and the same on both tags, a newline after the opening tag and before the
closing one. ShellBot types a long message into a tab as a paste. Measured:
  - 14 messages from another agent (A2A), each wrapped whole with nothing outside: the header, with
    its [[SF:TO:…]], on the first line inside, then 2 to 30-odd lines (1 short, 11 middling, 2 long);
  - 1 message of the person's own: three attached pictures, a blank line, then a paste of about 15
    lines that starts with a heading, and nothing after it.
Three more that the same rule covers: the person's own long message wrapped whole (typed in the chat
view, or pasted on its own), a long message from Telegram, wrapped whole, and a message the chain
limit stopped (agent_link.format_blocked: it still came, for reference only), wrapped whole.

Used by tests_chat_shell.py (the server's `from`, and the view's timeline) and tests_chat_a2a_ui.py.
"""
import agent_link

TG_ASK = "最終要回 Telegram 的文字請放在 [[TG_REPLY_ab12cd34]] 和 [[/TG_REPLY_ab12cd34]] 之間。"


def wrap(pid: str, inner: str) -> str:
    return f'<pasted_content id="{pid}">\n{inner}\n</pasted_content id="{pid}">'


def lines(n: int, word: str) -> str:
    return "\n".join(f"{word}，第 {i} 行。" for i in range(1, n + 1))


def a2a(pid: str, who: str, words: str) -> str:
    return wrap(pid, agent_link.format_delivery(who, words, lang="zh"))


def blocked(pid: str, who: str, words: str) -> str:
    return wrap(pid, agent_link.format_blocked(who, words, agent_link.MAX_DEPTH + 1, lang="zh"))


SHORT, MIDDLING, LONG = lines(3, "比較表整理好了"), lines(12, "測試都跑完了"), lines(31, "週報草稿")
NOTES = "# 會議重點\n" + lines(14, "貼上的筆記")
OWN_WHOLE = lines(6, "我自己打的一段長訊息")
FROM_PHONE = lines(5, "從手機傳的")

# (name, the text as the CLI got it, the `from` the server gives, what the view's timeline item holds)
SHAPES = [
    ("an A2A message, wrapped whole, 3 lines", a2a("9dd0", "研究", SHORT),
     {"kind": "a2a", "name": "研究", "handoff": False}, {"type": "peer", "who": "研究", "text": SHORT}),
    ("an A2A message, wrapped whole, 12 lines", a2a("3b7e", "寫程式", MIDDLING),
     {"kind": "a2a", "name": "寫程式", "handoff": False}, {"type": "peer", "who": "寫程式", "text": MIDDLING}),
    ("an A2A message, wrapped whole, 31 lines", a2a("e41c", "排程", LONG),
     {"kind": "a2a", "name": "排程", "handoff": False}, {"type": "peer", "who": "排程", "text": LONG}),
    ("the person's own: three pictures, then something pasted",
     "[Image #1] [Image #2] [Image #3]\n\n" + wrap("5c1e", NOTES),
     None, {"type": "user", "text": "[Image #1] [Image #2] [Image #3]\n\n" + NOTES,
            "parts": [{"text": "[Image #1] [Image #2] [Image #3]"}, {"paste": NOTES}]}),
    ("the person's own long message, wrapped whole", wrap("a7d3", OWN_WHOLE),
     None, {"type": "user", "text": OWN_WHOLE}),
    ("a long message from Telegram, wrapped whole", wrap("b210", TG_ASK + "\n\n" + FROM_PHONE),
     {"kind": "telegram"}, {"type": "user", "text": FROM_PHONE, "tags": [{"kind": "telegram"}]}),
    ("a message the chain limit stopped, wrapped whole, 3 lines", blocked("c9a2", "排程", SHORT),
     {"kind": "a2a", "name": "排程", "handoff": False, "blocked": True},
     {"type": "peer", "who": "排程", "text": SHORT, "blocked": True}),
]
