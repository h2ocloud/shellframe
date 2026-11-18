"""Which program a tab's first word starts on Windows.

A plain `bash` there is usually the WSL launcher in System32 (`C:\\Windows\\system32\\bash.exe`): with no
Linux distribution behind it, it prints a line and exits, and the tab is dead (measured on a machine that
had Git Bash installed as well). A tab that says `bash` wants a shell, so Git Bash is taken when it is
installed, and what PATH finds otherwise. A command with a path in it is the person's own choice.

Leaf module (stdlib only); the lookups are arguments so a test needs no Windows.
"""
import ntpath
import os
import shutil


def git_bash_candidates(which=shutil.which, env=None, path=None):
    env = os.environ if env is None else env
    out = []
    for var in ("ProgramFiles", "ProgramW6432", "ProgramFiles(x86)"):
        if env.get(var):
            out.append(ntpath.join(env[var], "Git", "bin", "bash.exe"))
    if env.get("LOCALAPPDATA"):
        out.append(ntpath.join(env["LOCALAPPDATA"], "Programs", "Git", "bin", "bash.exe"))
    git = which("git", path=path)                      # …\\Git\\cmd\\git.exe, or …\\Git\\mingw64\\bin\\git.exe
    if git:
        base = ntpath.dirname(ntpath.dirname(git))
        out += [ntpath.join(base, "bin", "bash.exe"), ntpath.join(ntpath.dirname(base), "bin", "bash.exe")]
    return out


def resolve(name, path=None, which=shutil.which, env=None, isfile=os.path.isfile):
    """The program for `name` (a command's first word): Git Bash for a plain `bash`, else what PATH finds
    (None when nothing)."""
    if str(name).lower() in ("bash", "bash.exe"):
        for cand in git_bash_candidates(which, env, path):
            if isfile(cand):
                return cand
    return which(name, path=path)
