#!/usr/bin/env python3
"""The one-line installers: what a new computer gets, and that they never stop silently.

install.sh runs for real here, but only as a dry run (SHELLFRAME_DRY_RUN=1) or against
stubs that fail on purpose, with HOME in a temporary folder and PATH holding only basic
tools plus stubs. So brew, git, python3 and tmux look missing even on a computer that
has them, `uname` can say Darwin or Linux, and nothing on this computer is installed or
changed. Each run has no controlling terminal and a pipe for stdin, as under
`curl | bash` with nobody to answer, and a timeout: a question that waits fails the test
instead of hanging it. One run gets a real terminal (a pty) to answer the AI CLI offer.

install.ps1 is checked statically, and parsed when PowerShell (pwsh) is on PATH.

Run: .venv/bin/python tests_install_scripts.py
"""
import os
import pty
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SH = HERE / "install.sh"
PS1 = HERE / "install.ps1"
BASH = "/bin/bash"           # macOS still ships bash 3.2: the script must run on it
passed = failed = 0


def check(name, ok, detail=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [PASS] {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}")
        for line in str(detail).splitlines()[-25:]:
            print(f"         {line}")


SYS_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
BASIC = ("sed grep tr head tail cat date sleep mkdir dirname basename rm awk cut env sort "
         "wc ls pgrep printf mv chmod").split()


def stub(bindir: Path, name: str, body: str):
    p = bindir / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)


def fake_env(tmp: Path, os_name: str, *, uid=1000, admin=True):
    """A bin folder with the basic tools and the stubs every case needs; returns its path."""
    bindir = tmp / "bin"
    bindir.mkdir()
    for tool in BASIC:
        real = shutil.which(tool, path=SYS_PATH)
        if real:
            (bindir / tool).symlink_to(real)
    stub(bindir, "uname", f'[ "$1" = "-m" ] && echo arm64 || echo {os_name}')
    groups = "staff admin" if admin else "staff"
    stub(bindir, "id", f'case "$1" in -u) echo {uid} ;; -Gn) echo "{groups}" ;; *) echo uid={uid} ;; esac')
    return bindir


def run_sh(tmp: Path, bindir: Path, *, lang="en", dry=True, extra=None, timeout=60):
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "PATH": str(bindir), "TERM": "dumb", "SHELLFRAME_LANG": lang,
           "SHELLFRAME_BREW_PREFIXES": str(tmp / "no-brew")}
    if dry:
        env["SHELLFRAME_DRY_RUN"] = "1"
    env.update(extra or {})
    # stdin is the script, as under `curl | bash`; no controlling terminal (new session).
    try:
        r = subprocess.run([BASH], input=SH.read_bytes(), env=env, capture_output=True,
                           timeout=timeout, start_new_session=True)
        return r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as e:
        return "timeout", (e.stdout or b"").decode("utf-8", "replace"), (e.stderr or b"").decode("utf-8", "replace")


def home_untouched(tmp: Path):
    return not any((tmp / "home").iterdir())


src = SH.read_text(encoding="utf-8")

# ── install.sh: structure ───────────────────────────────────
r = subprocess.run([BASH, "-n", str(SH)], capture_output=True, text=True)
check("install.sh parses with the system bash (macOS: 3.2)", r.returncode == 0, r.stderr)
lines = [ln for ln in src.splitlines() if ln.strip()]
check("everything runs from main() on the last line (a cut-off download runs nothing)",
      lines[-1] == 'main "$@"', lines[-1])
check("failures are trapped: set -E, an ERR trap and an EXIT trap that names the step",
      "set -Eeuo pipefail" in src and "trap on_err ERR" in src and "trap on_exit EXIT" in src
      and "stopped at" in src and "停在" in src)
installs = [ln.strip() for ln in src.splitlines()
            if re.search(r"\b(apt-get install|apt-get update|dnf install|pacman -S|brew\"? install)\b", ln)
            and not ln.strip().startswith("#") and "hint " not in ln and "請執行" not in ln and "Run:" not in ln]
bad = [ln for ln in installs if not re.match(r"(\w+\) )?(quiet_root_run|root_run|run) ", ln)]
check("every package install goes through run/root_run, so a dry run shows it instead of doing it",
      installs and not bad, bad or "no install lines found")
check("Homebrew's installer reads the terminal, not the pipe (it asks for Enter and the password)",
      '/bin/bash -c "$script" </dev/tty' in src and 'NONINTERACTIVE=1 /bin/bash -c "$script" </dev/null' in src)
check("answers are read from /dev/tty, never from stdin (which is the script)",
      "read -r ans </dev/tty" in src and not re.search(r"\bread -r? ?\w+ *$", src, re.M))
check("the macOS checks do not trust /usr/bin/git or /usr/bin/python3 (stubs on a new Mac)",
      'xcode-select -p' in src and '"$p/usr/bin/git"' in src and "|/usr/bin/*|" in src)
glued = [m.group(0) for m in re.finditer(r"\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])", src)]
check("no $name runs straight into Chinese text (bash in a UTF-8 locale reads those bytes as part of the name)",
      not glued, glued)
shellcheck = shutil.which("shellcheck")
if shellcheck:
    r = subprocess.run([shellcheck, "-s", "bash", "-S", "warning", str(SH)], capture_output=True, text=True)
    check("shellcheck: no warnings", r.returncode == 0, r.stdout)
else:
    print("  [info] shellcheck is not installed here; not run")

# ── install.sh: a new Mac, nothing installed ───────────────
with tempfile.TemporaryDirectory(prefix="sf-inst-") as d:
    tmp = Path(d)
    b = fake_env(tmp, "Darwin")
    stub(b, "xcode-select", "exit 2")          # no Command Line Tools
    rc, out, err = run_sh(tmp, b, lang="zh", extra={"LC_ALL": "en_US.UTF-8"})
    check("new Mac, dry run: finishes (exit 0), without waiting for an answer", rc == 0, f"rc={rc}\n{out}\n{err}")
    for name in ("Homebrew", "Command Line Tools", "Python 3.12", "tmux"):
        check(f"…announces 正在裝 {name} with the reason", re.search(rf"正在裝 {re.escape(name)}（.+）", out), out)
    check("…tells what to click in the Command Line Tools window and that it waits by itself",
          "請按「安裝」" in out and "裝好後自動繼續" in out, out)
    check("…prints the Command Line Tools installer it would open", "[dry-run] xcode-select --install" in out, out)
    check("…prints the brew installs it would run", "[dry-run] brew install -q python@3.12" in out
          and "[dry-run] brew install -q tmux" in out, out)
    check("…with no terminal, the AI CLI offer is skipped with a note, not asked",
          "沒有可以回答問題的終端機" in out, out)
    check("…and nothing is written to HOME", home_untouched(tmp), list((tmp / "home").rglob("*"))[:5])

# ── install.sh: a Mac that has everything ──────────────────
py_ok = sys.version_info >= (3, 10) and not os.path.realpath(sys.executable).startswith(
    ("/usr/bin/", "/Library/", "/System/", "/Applications/Xcode"))
with tempfile.TemporaryDirectory(prefix="sf-inst-") as d:
    tmp = Path(d)
    b = fake_env(tmp, "Darwin")
    prefix = tmp / "brew"
    (prefix / "bin").mkdir(parents=True)
    stub(prefix / "bin", "brew", 'echo "brew called: $*" >&2; [ "$1" = shellenv ] && exit 0; exit 9')
    stub(prefix / "bin", "tmux", "exit 0")
    if py_ok:
        (prefix / "bin" / "python3").symlink_to(sys.executable)
    clt = tmp / "clt"
    (clt / "usr" / "bin").mkdir(parents=True)
    stub(clt / "usr" / "bin", "git", "exit 0")
    stub(b, "xcode-select", f'[ "$1" = "-p" ] && echo {clt} && exit 0; exit 1')
    rc, out, err = run_sh(tmp, b, extra={"SHELLFRAME_BREW_PREFIXES": str(prefix)})
    check("a Mac with Homebrew, the tools and tmux: nothing is reinstalled (safe to run again)",
          rc == 0 and "Installing" not in out.replace("Installing ShellBot", "") and "brew called: install" not in err,
          f"rc={rc}\n{out}\n{err}")
    if py_ok:
        check("…and its Homebrew Python is used", f"Using {prefix}/bin/python3" in out, out)
    else:
        print("  [info] this Python is Apple's or older than 3.10; the 'uses Homebrew Python' check was not run")

# ── install.sh: a new Debian/Ubuntu, nothing installed ─────
with tempfile.TemporaryDirectory(prefix="sf-inst-") as d:
    tmp = Path(d)
    b = fake_env(tmp, "Linux")
    stub(b, "apt-get", "exit 0")
    stub(b, "dpkg-query", "exit 1")            # nothing installed
    stub(b, "sudo", 'exit 0')
    rc, out, err = run_sh(tmp, b)
    check("new Debian/Ubuntu, dry run: finishes (exit 0)", rc == 0, f"rc={rc}\n{out}\n{err}")
    for pkg in ("git", "python3-venv", "tmux", "python3-gi", "gir1.2-gtk-3.0", "gir1.2-webkit2-4.1"):
        check(f"…announces Installing {pkg} with the reason", re.search(rf"Installing {re.escape(pkg)} \(.+\)", out), out)
    check("…one apt-get install with all of them, through sudo -n when nobody can type a password",
          re.search(r"\[dry-run\] sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install -y .*python3-venv.*gir1.2-webkit2-4.1", out),
          out)
    check("…and nothing is written to HOME", home_untouched(tmp))

with tempfile.TemporaryDirectory(prefix="sf-inst-") as d:
    tmp = Path(d)
    b = fake_env(tmp, "Linux")
    stub(b, "apt-get", f'echo "$@" >> {tmp}/apt-calls; exit 0')
    stub(b, "dpkg-query", "printf 'install ok installed'")
    if py_ok:
        (b / "python3").symlink_to(sys.executable)
    rc, out, err = run_sh(tmp, b)
    check("Debian/Ubuntu with everything: no apt-get call at all (safe to run again)",
          rc == 0 and "Everything is already installed" in out and not (tmp / "apt-calls").exists(),
          f"rc={rc}\n{out}\n{err}")

# ── install.sh: failures say where and how to finish ───────
with tempfile.TemporaryDirectory(prefix="sf-inst-") as d:
    tmp = Path(d)
    b = fake_env(tmp, "Linux", uid=0)
    stub(b, "apt-get", 'echo "E: Unable to locate package" >&2; exit 100')
    stub(b, "dpkg-query", "exit 1")
    rc, out, err = run_sh(tmp, b, dry=False)
    check("apt-get failing (as root, for real): exits non-zero", rc not in (0, "timeout"), f"rc={rc}")
    check("…naming the step it stopped at, the command, and the manual fix",
          'stopped at "System packages"' in err and "apt-get" in err and "sudo apt-get install -y" in err
          and "paste the same install line again" in err, err)
    check("…before touching HOME", home_untouched(tmp))

with tempfile.TemporaryDirectory(prefix="sf-inst-") as d:
    tmp = Path(d)
    b = fake_env(tmp, "Linux")
    stub(b, "apt-get", "exit 0")
    stub(b, "dpkg-query", "exit 1")
    stub(b, "sudo", 'echo "sudo: a password is required" >&2; exit 1')
    rc, out, err = run_sh(tmp, b, dry=False, lang="zh")
    check("sudo needing a password with nobody to type it: a clear stop, not a hang",
          rc not in (0, "timeout") and "沒有可以輸入密碼的終端機" in err and "停在「系統套件」" in err, f"rc={rc}\n{err}")

with tempfile.TemporaryDirectory(prefix="sf-inst-") as d:
    tmp = Path(d)
    b = fake_env(tmp, "Linux")
    rc, out, err = run_sh(tmp, b, dry=False)
    check("no apt, dnf or pacman: says so and lists what to install by hand",
          rc not in (0, "timeout") and "No supported package manager" in err and "WebKit2GTK" in err, f"rc={rc}\n{err}")

with tempfile.TemporaryDirectory(prefix="sf-inst-") as d:
    tmp = Path(d)
    b = fake_env(tmp, "Darwin", admin=False)
    stub(b, "xcode-select", "exit 2")
    rc, out, err = run_sh(tmp, b, dry=False)
    check("a Mac account that is not an administrator: stops before Homebrew, saying why",
          rc not in (0, "timeout") and "not an administrator" in err and 'stopped at "Homebrew' in err, f"rc={rc}\n{err}")

# ── install.sh: with a terminal, under curl | bash ─────────
def run_in_pty(tmp: Path, bindir: Path, answers, timeout=60):
    """Run `cat install.sh | bash` with a pty as the terminal; answer each prompt in turn."""
    home = tmp / "home"
    home.mkdir(exist_ok=True)
    env = {"HOME": str(home), "PATH": str(bindir), "TERM": "dumb", "SHELLFRAME_LANG": "en",
           "SHELLFRAME_DRY_RUN": "1", "SHELLFRAME_BREW_PREFIXES": str(tmp / "no-brew")}
    pid, fd = pty.fork()
    if pid == 0:
        os.execve(BASH, [BASH, "-c", f'cat "{SH}" | {BASH}'], env)
    out, pending, deadline = b"", list(answers), time.time() + timeout
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 0.5)
        if r:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
        if pending and out.decode("utf-8", "replace").count("[y/N]") > len(answers) - len(pending):
            os.write(fd, pending.pop(0).encode() + b"\n")
    try:
        _, status = os.waitpid(pid, 0)
        code = os.waitstatus_to_exitcode(status)
    except ChildProcessError:
        code = None
    os.close(fd)
    return code, out.decode("utf-8", "replace")


with tempfile.TemporaryDirectory(prefix="sf-inst-") as d:
    tmp = Path(d)
    b = fake_env(tmp, "Darwin")
    stub(b, "xcode-select", "exit 2")
    code, out = run_in_pty(tmp, b, ["y", "n"])
    check("with a terminal under curl | bash: Homebrew's Enter and password are explained first",
          "press Enter" in out and "Password:" in out and "login password" in out, out)
    check("…the AI CLIs are offered, answers read from the terminal (stdin is the script)",
          "Also install Claude Code now? [y/N]" in out and "Also install Codex (OpenAI) now? [y/N]" in out, out)
    check("…yes to Claude Code runs its official installer; no to Codex installs nothing",
          "[dry-run] bash -c curl -fsSL https://claude.ai/install.sh | bash" in out
          and "npm install -g @openai/codex" not in out and "Installing Node.js" not in out, out)
    check("…and finishes", code == 0 and "Dry run finished" in out, f"code={code}")

# ── install.ps1 ─────────────────────────────────────────────
raw = PS1.read_bytes()
ps = raw.decode("utf-8")
check("install.ps1 is UTF-8 without a BOM (irm decodes it; a BOM would reach iex as text)",
      not raw.startswith(b"\xef\xbb\xbf"))
code_lines = [ln for ln in ps.splitlines() if not ln.lstrip().startswith("#")]
check("install.ps1 never calls exit (under irm | iex it would close the user's window)",
      not [ln for ln in code_lines if re.search(r"(^|[;{]\s*)exit\b", ln.strip())])
check("…runs inside & { } with one try/catch that names the step and the manual fix",
      re.search(r"^& \{$", ps, re.M) and "} catch {" in ps
      and "stopped at" in ps and "停在" in ps and "$S.Hint" in ps)
check("…checks native exit codes: git, venv and pip go through Invoke-Tool, whose code is tested",
      "Invoke-Tool $venvPy @(\"-m\", \"pip\", \"install\", \"-q\", \"-r\"" in ps
      and re.search(r'Git-Run @\("clone"', ps) and "if ($code -ne 0) { Fail" in ps)
check("…announces the UAC prompt before installs that can raise one (Git, WebView2, Node.js)",
      ps.count("Say-Uac") >= 4 and "請按「是」" in ps)
check("…works without winget: direct official downloads for Git, Python and WebView2",
      "git-for-windows/git/releases/latest" in ps and "www.python.org/ftp/python/" in ps
      and "go.microsoft.com/fwlink/p/?LinkId=2124703" in ps)
check("…Python per user (no admin), never the Microsoft Store stub",
      '"--scope", "user"' in ps and "InstallAllUsers=0" in ps and "WindowsApps" in ps)
check("…asks only in an interactive console, and runs installers that may exit in a child PowerShell",
      "if (-not $CanAsk) { return $false }" in ps and '"powershell.exe" @("-NoProfile"' in ps and '"npm.cmd"' in ps)
check("…honours the same SHELLFRAME_* switches as install.sh",
      all(f"$env:SHELLFRAME_{k}" in ps for k in ("REF", "REPO", "LANG", "DRY_RUN", "NONINTERACTIVE",
                                                  "INSTALL_CLIS", "NO_LAUNCH")))
pwsh = os.environ.get("SHELLFRAME_PWSH") or shutil.which("pwsh")
if pwsh:
    r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command",
                        "$e=$null;$t=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
                        f"'{PS1}',[ref]$t,[ref]$e);$e.Count;$e|%{{$_.Extent.StartLineNumber.ToString()+': '+$_.Message}}"],
                       capture_output=True, text=True, timeout=120)
    check("install.ps1 parses in PowerShell (no syntax errors)", r.stdout.strip().splitlines()[:1] == ["0"],
          r.stdout + r.stderr)
    # A dry run through irm | iex's path (the text into Invoke-Expression). Off Windows nothing
    # is found (no winget, no registry), so every install step is announced; the window survives.
    with tempfile.TemporaryDirectory(prefix="sf-inst-ps-") as d:
        env = dict(os.environ, USERPROFILE=d, TEMP=d, SHELLFRAME_DRY_RUN="1", SHELLFRAME_LANG="en")
        r = subprocess.run([pwsh, "-NoProfile", "-NonInteractive", "-Command",
                            f"Get-Content -Raw -Encoding UTF8 '{PS1}' | Invoke-Expression; 'window-still-open'"],
                           capture_output=True, text=True, timeout=120, env=env)
        out = r.stdout
        check("install.ps1 dry run (pwsh): announces each missing piece, finishes, leaves the window open",
              "Installing Python 3.12 (" in out and "Installing WebView2 Runtime (" in out
              and "Dry run finished" in out and out.rstrip().endswith("window-still-open")
              and not any(Path(d).iterdir()), out + r.stderr)
else:
    print("  [info] pwsh is not on PATH; install.ps1 was checked statically only")

print(f"\nResults: {passed} passed, {failed} failed")
print("ALL PASS" if not failed else f"{failed} FAILED")
sys.exit(1 if failed else 0)
