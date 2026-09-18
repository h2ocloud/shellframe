"""
ai_skill — make the ShellFrame reference something an agent finds by itself.

`docs/ai-skill.md` tells an agent how to drive ShellFrame, but a document only
helps if the agent reads it, and asking the user to paste it into every new tab
does not scale. Claude Code already looks in `~/.claude/skills/*/SKILL.md` on its
own, so ShellFrame installs a skill there and the discovery is automatic.

What gets installed is a **pointer, not a copy**. The file says what ShellFrame
is in a few lines and then tells the agent to run `sfctl skill`, which prints the
reference off the installed build with its real version and experimental flags on
top. A copy would be wrong the first time ShellFrame is updated; a pointer never
goes stale.

Two targets, deliberately treated differently:

  * `~/.claude/skills/shellframe/SKILL.md` — a directory nothing but ShellFrame
    owns, so writing it unprompted is safe and it is rewritten on every start to
    track the installed path.

  * `~/.codex/AGENTS.md` — a file the user writes in. Only touched when asked,
    and only inside a marked block that is replaced rather than appended, so
    installing twice leaves one copy and removing it leaves the file intact.
"""

import os
import re
import shutil
from pathlib import Path

CLAUDE_SKILL = Path.home() / ".claude" / "skills" / "shellframe" / "SKILL.md"
CODEX_AGENTS = Path.home() / ".codex" / "AGENTS.md"

MARK_START = "<!-- shellframe:skill:start -->"
MARK_END = "<!-- shellframe:skill:end -->"

DESCRIPTION = (
    "在 ShellFrame 分頁裡工作時怎麼操作 ShellFrame：看/控制其他分頁、"
    "delegate 給角色、角色群組、跨機器操控另一台電腦的 session。"
    "觸發時機：使用者要你看別的分頁、派工給其他 agent、跨機器操作，"
    "或提到 ShellFrame / sfctl / Frame Link。"
)


def sfctl_path() -> str:
    """How to invoke sfctl from an arbitrary cwd.

    `sfctl` is only a bare word if ~/.local/bin is on PATH, which is not
    guaranteed inside every shell ShellFrame opens. Prefer the resolved absolute
    path so the instruction works regardless."""
    found = shutil.which("sfctl")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "sfctl"
    if local.exists():
        return str(local)
    return "sfctl"


def pointer_body() -> str:
    """The prose both targets share. No version numbers and no command list —
    those live in the doc `sfctl skill` prints, which cannot go out of date."""
    cli = sfctl_path()
    return (
        "You are running inside a **ShellFrame** tab. ShellFrame is a multi-tab\n"
        "terminal workspace where each tab is a tmux-backed shell, usually an AI\n"
        "CLI. From your tab you can read and drive the other tabs, delegate work\n"
        "to a named role, message other agents, and — once two computers are\n"
        "paired over Frame Link — do the same to another machine's tabs.\n"
        "\n"
        "**Before doing any of that, run this and read what comes back:**\n"
        "\n"
        "```bash\n"
        f"{cli} skill\n"
        "```\n"
        "\n"
        "It prints the full reference for the build that is actually installed\n"
        "here, stamped with its version and with which experimental features are\n"
        "switched on. Do not work from a remembered command list: features are\n"
        "added often and half of them are off by default, so a command that\n"
        "exists in one ShellFrame refuses in another.\n"
        "\n"
        f"If `{cli}` is not found, ShellFrame is not installed on this machine\n"
        "and none of the above applies. Say so rather than guessing.\n"
        "\n"
        "One thing worth knowing before you act: every tab runs with permissions\n"
        "bypassed, so sending text into another tab is a real instruction to a\n"
        "real agent, including across the network. Say what you are about to do\n"
        "before you do it.\n"
    )


def claude_skill_text() -> str:
    return (f"---\nname: shellframe\ndescription: {DESCRIPTION}\n---\n\n"
            f"# ShellFrame\n\n{pointer_body()}")


def install_claude_skill() -> tuple:
    """Write the personal skill Claude Code auto-discovers.

    Returns (changed, path_or_error). Rewrites only when the content differs, so
    a start-up call is cheap and leaves the mtime alone when nothing moved."""
    want = claude_skill_text()
    try:
        if CLAUDE_SKILL.exists() and CLAUDE_SKILL.read_text(encoding="utf-8") == want:
            return False, str(CLAUDE_SKILL)
        CLAUDE_SKILL.parent.mkdir(parents=True, exist_ok=True)
        tmp = CLAUDE_SKILL.with_suffix(".tmp")
        tmp.write_text(want, encoding="utf-8")
        os.replace(tmp, CLAUDE_SKILL)
        return True, str(CLAUDE_SKILL)
    except Exception as e:
        return False, f"寫入失敗：{e}"


def _marked_block() -> str:
    return (f"{MARK_START}\n## ShellFrame\n\n{pointer_body()}{MARK_END}\n")


def apply_marked_block(text: str, block: str) -> str:
    """Replace an existing ShellFrame block, or append one. Idempotent: applying
    the same block twice gives the same file."""
    pattern = re.compile(re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\n?",
                         re.DOTALL)
    if pattern.search(text):
        return pattern.sub(block, text, count=1)
    sep = "" if (not text or text.endswith("\n\n")) else ("\n" if text.endswith("\n") else "\n\n")
    return text + sep + block


def remove_marked_block(text: str) -> str:
    pattern = re.compile(r"\n*" + re.escape(MARK_START) + r".*?" + re.escape(MARK_END) + r"\n?",
                         re.DOTALL)
    return pattern.sub("", text)


def install_codex_agents(remove: bool = False) -> tuple:
    """Add (or take out) the ShellFrame block in ~/.codex/AGENTS.md.

    Unlike the Claude skill this is a file the user also writes in, so it is only
    touched on request and only inside the markers."""
    try:
        if not CODEX_AGENTS.parent.exists():
            return False, "找不到 ~/.codex，這台機器沒有裝 Codex"
        old = CODEX_AGENTS.read_text(encoding="utf-8") if CODEX_AGENTS.exists() else ""
        new = remove_marked_block(old) if remove else apply_marked_block(old, _marked_block())
        if new == old:
            return False, str(CODEX_AGENTS)
        CODEX_AGENTS.parent.mkdir(parents=True, exist_ok=True)
        tmp = CODEX_AGENTS.with_suffix(".tmp")
        tmp.write_text(new, encoding="utf-8")
        os.replace(tmp, CODEX_AGENTS)
        return True, str(CODEX_AGENTS)
    except Exception as e:
        return False, f"寫入失敗：{e}"


def status() -> dict:
    """What the UI shows: whether each target currently carries the pointer."""
    claude_ok = False
    try:
        claude_ok = (CLAUDE_SKILL.exists()
                     and "sfctl skill" in CLAUDE_SKILL.read_text(encoding="utf-8"))
    except Exception:
        pass
    codex_ok = False
    try:
        codex_ok = CODEX_AGENTS.exists() and MARK_START in CODEX_AGENTS.read_text(encoding="utf-8")
    except Exception:
        pass
    return {"claude": claude_ok, "claude_path": str(CLAUDE_SKILL),
            "codex": codex_ok, "codex_path": str(CODEX_AGENTS),
            "codex_available": CODEX_AGENTS.parent.exists(),
            "sfctl": sfctl_path()}
