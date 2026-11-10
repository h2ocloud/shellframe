"""cli_install — the install command for an AI CLI on this platform.

The provider registry (usage_probe.PROVIDER_SPECS) keeps one command per CLI,
written for macOS and Linux: mostly `curl … | bash`. On Windows the install
button opens a PowerShell tab (api_accounts.install_provider), where that pipeline
cannot work, so a new Windows user who pressed "install" for Claude Code got an
error instead of Claude Code.

On Windows the command is the CLI's own PowerShell installer where it publishes
one, checked against its documentation and the script it serves (2026-11-10):

  claude   https://claude.ai/install.ps1            (Claude Code setup docs)
  agy      https://antigravity.google/cli/install.ps1  (Antigravity CLI install docs)
  grok     https://x.ai/cli/install.ps1             (usage line in the script itself)

A command that is not a shell pipeline (npm, PowerShell's irm) stays as it is.
Anything else is not offered: the dialog shows the note and the documentation
link instead of a command that cannot run. OpenCode is in that group; its docs say
Windows package managers are not supported and point to a downloaded binary.
"""

import re

import agent_cursor

WINDOWS = {
    "claude": "irm https://claude.ai/install.ps1 | iex",
    "agy": "irm https://antigravity.google/cli/install.ps1 | iex",
    "grok": "irm https://x.ai/cli/install.ps1 | iex",
    # the registry resolves this one when it is imported; asked here for Windows explicitly
    "cursor": agent_cursor.install_command(True),
}
WINDOWS_NOTES = {
    "codex": "Windows 要先裝 Node.js（ShellBot 的安裝程式不會裝），再用 npm 安裝。",
    "pi": "需要 Node.js 22.19 以上（ShellBot 的安裝程式不會裝）。",
    "opencode": "Windows 沒有內建的安裝指令：官方文件寫明 Windows 的套件管理器不支援，"
                "請照文件下載 Windows 版執行檔。",
}
_MAC_NOTES = {"agy"}          # the registry's note describes the macOS / Linux installer
AFTER_INSTALL = ("裝完如果還是說找不到，請從桌面捷徑重開 ShellBot："
                 "Windows 不會把新的 PATH 交給已經在跑的程式。")
_SHELL_PIPE = re.compile(r"\|\s*(?:ba|z)?sh\b")


def for_platform(provider: str, install, windows: bool) -> dict:
    """The registry's install dict, adjusted for Windows. Never a bash pipeline there."""
    out = dict(install or {})
    if not windows:
        return out
    cmd = WINDOWS.get(provider) or out.get("command") or ""
    if _SHELL_PIPE.search(cmd):
        cmd = ""
    note = WINDOWS_NOTES.get(provider) or ("" if provider in _MAC_NOTES else out.get("note", ""))
    if cmd:
        out["command"] = cmd
        note = f"{note} {AFTER_INSTALL}".strip()
    else:
        out.pop("command", None)
    if note:
        out["note"] = note
    else:
        out.pop("note", None)
    return out
