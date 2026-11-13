#!/bin/bash
# ShellBot (formerly ShellFrame) installer for macOS and Linux.
#
#   curl -fsSL https://raw.githubusercontent.com/h2ocloud/shellframe/main/install.sh | bash
#
# One line from a new computer to ShellBot open. It installs what is missing, leaves
# what is already there, and is safe to run again (running it again is also how an
# install is updated):
#
#   macOS  Homebrew, which brings Apple's Command Line Tools (git, clang); Python 3.12; tmux
#   Linux  git, Python 3 with venv, tmux, and the GTK + WebKit bindings the window is
#          drawn with (apt on Debian/Ubuntu, dnf on Fedora, pacman on Arch)
#
# What may need you: on a Mac without Homebrew, its installer asks you to press Enter and
# for your login password, and macOS may show a window to install the Command Line Tools;
# on Linux, sudo asks for your password. Each is announced before it happens. AI CLIs
# (Claude Code, Codex, ...) are optional: the end of the install offers them.
#
# Environment, all optional:
#   SHELLFRAME_REF=<branch>        install this branch instead of main
#   SHELLFRAME_REPO=<url|path>     clone from here instead of GitHub
#   SHELLFRAME_LANG=en|zh          language of the messages (default: the system's)
#   SHELLFRAME_DRY_RUN=1           check and print what would be installed; change nothing
#   SHELLFRAME_NONINTERACTIVE=1    ask nothing and take the default answers
#   SHELLFRAME_INSTALL_CLIS=claude,codex | none   answer the AI CLI question in advance
#   SHELLFRAME_NO_LAUNCH=1         do not open ShellBot at the end of a first install
#   SHELLFRAME_SKIP_DOCK=1         do not pin ShellBot in the Dock (macOS)
#   SHELLFRAME_BREW_PREFIXES="/opt/homebrew /usr/local"   where to look for Homebrew
#
# Everything runs from main() on the last line: a download cut short runs nothing, and
# no command can read the rest of this script from the pipe it arrives on.

set -Eeuo pipefail

INSTALL_DIR="${HOME}/.local/apps/shellframe"
BIN_DIR="${HOME}/.local/bin"
REPO_URL="${SHELLFRAME_REPO:-https://github.com/h2ocloud/shellframe.git}"
REF="${SHELLFRAME_REF:-main}"
DRY_RUN="${SHELLFRAME_DRY_RUN:-0}"
BREW_INSTALL_URL="https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh"
OS="$(uname -s)"

STEP=""            # the step being worked on, named in the failure message
HINT=""            # how to finish that step by hand
LAST_CMD=""        # the last command run, also named on failure
DONE=0
CAN_ASK=0          # 1 = a person is at a terminal we can read answers from
ROOT_CMD=""        # "", "sudo" or "sudo -n": how Linux package installs get root
PM=""              # Linux package manager: apt, dnf or pacman
BREW=""            # path of brew (macOS)
PYTHON=""          # interpreter the venv is built from
VENV_PY="${INSTALL_DIR}/.venv/bin/python"
APP_DEST=""
FRESH=0            # 1 = no install was here before this run
INSTALLED=""       # what this run installed, for the summary
NEXT=""            # next steps to print at the end
START_TS="$(date +%s)"
UI_LANG="en"

# ── Messages ────────────────────────────────────────────────

detect_lang() {
  local tag="${SHELLFRAME_LANG:-}"
  if [ -z "$tag" ] && [ "$OS" = "Darwin" ]; then
    tag="$(defaults read -g AppleLanguages 2>/dev/null | sed -n '2p' | tr -d ' ",' || true)"
  fi
  [ -n "$tag" ] || tag="${LC_ALL:-${LC_MESSAGES:-${LANG:-}}}"
  case "$tag" in zh*|ZH*) UI_LANG="zh" ;; *) UI_LANG="en" ;; esac
}

t() { if [ "$UI_LANG" = "zh" ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
say() { printf '%s\n' "$(t "$1" "$2")"; }
note() { printf '    %s\n' "$(t "$1" "$2")"; }
warn() { printf '    ! %s\n' "$(t "$1" "$2")"; }
hint() { HINT="$(t "$1" "$2")"; }
step() {
  STEP="$(t "$1" "$2")"
  HINT=""
  LAST_CMD=""
  printf '\n==> %s\n' "$STEP"
}
# installing <name> <why, English> <why, Chinese>
installing() {
  printf '    %s\n' "$(t "Installing $1 ($2)" "正在裝 $1（$3）")"
  INSTALLED="${INSTALLED:+$INSTALLED, }$1"
}
have() { command -v "$1" >/dev/null 2>&1; }

# Stop with a reason; the EXIT trap adds the step, the last command and the hint.
die() {
  printf '\n    ✗ %s\n' "$(t "$1" "$2")" >&2
  exit 1
}

# Every command that changes the computer goes through run (or root_run), so a dry run
# shows it instead of doing it.
run() {
  LAST_CMD="$*"
  if [ "$DRY_RUN" = "1" ]; then
    printf '    [dry-run] %s\n' "$*"
    return 0
  fi
  "$@"
}
root_run() {
  if [ -z "$ROOT_CMD" ]; then run "$@"; return; fi
  # shellcheck disable=SC2086  # ROOT_CMD is "sudo" or "sudo -n", split on purpose
  run $ROOT_CMD "$@"
}

# A long, chatty package install: its output goes to a log, shown only if it fails.
PKG_LOG=""
quiet_root_run() {
  local rc=0
  if [ "$DRY_RUN" = "1" ]; then root_run "$@"; return; fi
  if [ -z "$PKG_LOG" ]; then
    PKG_LOG="$(mktemp "${TMPDIR:-/tmp}/shellbot-install.XXXXXX")"
    note "(this can take a few minutes; the details go to $PKG_LOG)" "（可能要幾分鐘；詳細過程記在 ${PKG_LOG}）"
  fi
  root_run "$@" >>"$PKG_LOG" 2>&1 || rc=$?
  if [ "$rc" != "0" ]; then
    tail -n 25 "$PKG_LOG" >&2
    return "$rc"
  fi
}

# The command named on failure: the one this step last ran, written out with its values
# (run and the steps set it); only when there is none, the line that failed, as written.
on_err() { [ -n "$LAST_CMD" ] || LAST_CMD="${BASH_COMMAND}"; }
on_exit() {
  local rc=$?
  [ "$DONE" = "1" ] && return 0
  [ "$rc" = "0" ] && return 0
  {
    printf '\n✗ %s\n' "$(t "ShellBot was not installed: it stopped at \"${STEP:-start}\"." \
                             "ShellBot 沒有裝完：停在「${STEP:-開始}」。")"
    [ -n "$LAST_CMD" ] && printf '  %s %s\n' "$(t "Last command:" "最後執行的指令：")" "$LAST_CMD"
    [ -n "$HINT" ] && printf '  %s\n' "$HINT"
    printf '  %s\n' "$(t "Once that is fixed, paste the same install line again: what is already done is skipped." \
                          "補好之後再貼一次同一行安裝指令即可：已經裝好的會跳過。")"
  } >&2
}
trap on_err ERR
trap on_exit EXIT
trap 'exit 130' INT TERM

# A person can answer only if this is a terminal they are watching (stdout) and the
# terminal can be opened: under `curl | bash` stdin is the script itself, so answers
# are read from /dev/tty. In `docker run` without -t, /dev/tty exists but cannot be
# opened, and the in-app repair runs with output captured; both take the defaults.
detect_tty() {
  CAN_ASK=0
  [ "${SHELLFRAME_NONINTERACTIVE:-0}" = "1" ] && return 0
  [ -t 1 ] || return 0
  if (: </dev/tty) 2>/dev/null; then CAN_ASK=1; fi
}

# ask <question en> <question zh>: true for yes. No terminal means no.
ask() {
  local ans=""
  [ "$CAN_ASK" = "1" ] || return 1
  printf '    %s ' "$(t "$1" "$2")" >/dev/tty
  IFS= read -r ans </dev/tty || ans=""
  case "$ans" in y|Y|yes|YES|Yes|是|好|要) return 0 ;; *) return 1 ;; esac
}


# ── macOS ───────────────────────────────────────────────────

brew_path() {
  local p
  if have brew; then command -v brew; return 0; fi
  for p in ${SHELLFRAME_BREW_PREFIXES:-/opt/homebrew /usr/local}; do
    if [ -x "$p/bin/brew" ]; then printf '%s\n' "$p/bin/brew"; return 0; fi
  done
  return 1
}

use_brew() {
  local b
  b="$(brew_path)" || return 1
  BREW="$b"
  eval "$("$BREW" shellenv)"
}

is_admin() { id -Gn 2>/dev/null | tr ' ' '\n' | grep -qx admin; }

# The Command Line Tools are installed when xcode-select points at a folder with git in
# it. /usr/bin/git alone proves nothing: on a new Mac it is a stub that opens the
# installer window and fails.
clt_ok() {
  local p
  p="$(xcode-select -p 2>/dev/null)" || return 1
  [ -n "$p" ] && [ -x "$p/usr/bin/git" ]
}

# Homebrew's own installer writes ~/.zprofile only in its advice. ShellBot.app and the
# shellframe launcher read ~/.zprofile, so brew must be on PATH there.
persist_brew_env() {
  local prefix rc="$HOME/.zprofile"
  prefix="$(dirname "$(dirname "$BREW")")"
  [ "$prefix" = "/usr/local" ] && return 0
  if ! grep -qs 'brew shellenv' "$rc"; then
    LAST_CMD="append brew shellenv to $rc"
    if [ "$DRY_RUN" != "1" ]; then
      # shellcheck disable=SC2016  # the line is written literally, for the shell that reads ~/.zprofile
      printf '\neval "$(%s shellenv)"\n' "$BREW" >>"$rc"
    fi
    note "Homebrew added to PATH in ~/.zprofile" "已把 Homebrew 加進 ~/.zprofile 的 PATH"
  fi
}

wait_for_clt() {
  local start now waited seen=0 gone_since=0 last_say=0
  start="$(date +%s)"
  while ! clt_ok; do
    now="$(date +%s)"
    waited=$(( now - start ))
    if pgrep -qf "Install Command Line Developer Tools" 2>/dev/null; then
      seen=1; gone_since=0
    elif [ "$seen" = "1" ]; then
      [ "$gone_since" = "0" ] && gone_since="$now"
      # The window closed and the tools are still not there: cancelled, or it failed.
      if [ $(( now - gone_since )) -ge 30 ]; then
        die "The Command Line Tools window closed but the tools are not installed (cancelled, or the download failed)." \
            "命令列工具的安裝視窗關掉了，但工具沒有裝好（按了取消，或下載失敗）。"
      fi
    fi
    if [ "$waited" -ge 3600 ]; then
      die "Still no Command Line Tools after an hour." "等了一小時，命令列工具還沒裝好。"
    fi
    if [ $(( waited - last_say )) -ge 60 ]; then
      last_say="$waited"
      if [ "$seen" = "1" ] || [ "$waited" -lt 60 ]; then
        note "Waiting for the Command Line Tools to finish ($(( waited / 60 )) min so far)..." \
             "等待命令列工具裝完（已等 $(( waited / 60 )) 分鐘）…"
      else
        note "No installer window yet. If you do not see one, run in another Terminal window: xcode-select --install" \
             "還沒看到安裝視窗。如果畫面上沒有，請在另一個終端機視窗執行：xcode-select --install"
      fi
    fi
    sleep 5
  done
  note "Command Line Tools installed." "命令列工具裝好了。"
}

install_clt_with_dialog() {
  installing "Command Line Tools" "git and the compiler for the app launcher are in them" \
             "git 和 App 啟動器要用的編譯器都在裡面"
  hint "Run: xcode-select --install, click Install, wait until it says the software was installed, then run the install line again." \
       "請執行 xcode-select --install，按「安裝」，等它顯示「已安裝軟體」後，再貼一次安裝指令。"
  say "    A macOS window will ask whether to install the command line developer tools:" \
      "    macOS 會跳出視窗，問你要不要安裝「命令列開發者工具」："
  say "      click \"Install\", then \"Agree\". The download takes about 5 to 15 minutes;" \
      "      請按「安裝」，再按「同意」。下載安裝大約 5 到 15 分鐘，"
  say "      this installer waits and goes on by itself when it is done." \
      "      這裡會一直等，裝好後自動繼續，不用回來按任何鍵。"
  run xcode-select --install >/dev/null 2>&1 || true
  [ "$DRY_RUN" = "1" ] && return 0
  wait_for_clt
}

install_homebrew() {
  local script rc=0
  step "Homebrew (the macOS package manager)" "Homebrew（macOS 的套件管理工具）"
  if use_brew; then note "Already installed: $BREW" "已經有了：$BREW"; return 0; fi
  installing "Homebrew" "Python and tmux are installed with it; it also brings Apple's Command Line Tools (git, clang)" \
             "用它來裝 Python 和 tmux；它也會順便裝 Apple 的命令列工具（git、clang）"
  hint "Install Homebrew by hand in Terminal, then paste the install line again: /bin/bash -c \"\$(curl -fsSL $BREW_INSTALL_URL)\"" \
       "請在「終端機」手動安裝 Homebrew，再貼一次安裝指令：/bin/bash -c \"\$(curl -fsSL $BREW_INSTALL_URL)\""
  if ! is_admin && [ "$DRY_RUN" = "1" ]; then
    warn "This account is not an administrator: a real run stops here (Homebrew needs one)." \
         "這個帳號不是管理員：實際安裝會停在這裡（Homebrew 需要管理員）。"
  elif ! is_admin; then
    die "This macOS account is not an administrator, and Homebrew needs one. Run this line once from an administrator account (or ask whoever manages this Mac), then again here." \
        "這個 macOS 帳號不是管理員，Homebrew 需要管理員權限。請用管理員帳號跑一次這行指令（或請管理這台 Mac 的人協助），再回到這個帳號重跑。"
  fi
  if [ "$CAN_ASK" = "1" ]; then
    say "    What happens next:" "    接下來會發生："
    say "      1. Homebrew lists what it will install and asks you to press RETURN/ENTER: press Enter." \
        "      1. Homebrew 會列出要裝的東西，並說「Press RETURN/ENTER」：請按 Enter。"
    say "      2. \"Password:\" asks for this Mac's login password. Type it and press Enter (nothing shows while you type; that is normal)." \
        "      2. 出現「Password:」時，輸入這台 Mac 的登入密碼再按 Enter（打字時畫面不會顯示，這是正常的）。"
    say "      3. If Apple's Command Line Tools are missing, Homebrew downloads them too: 5 to 15 minutes." \
        "      3. 如果還沒有 Apple 命令列工具，Homebrew 會一起下載，大約 5 到 15 分鐘。"
  fi
  if [ "$DRY_RUN" = "1" ]; then
    run /bin/bash -c "\$(curl -fsSL $BREW_INSTALL_URL)"
    BREW="brew"
    return 0
  fi
  LAST_CMD="curl -fsSL $BREW_INSTALL_URL"
  script="$(curl -fsSL "$BREW_INSTALL_URL")"
  [ -n "$script" ] || die "Could not download the Homebrew installer (network?)." "下載不到 Homebrew 安裝程式（網路問題？）。"
  LAST_CMD="Homebrew install.sh"
  if [ "$CAN_ASK" = "1" ]; then
    /bin/bash -c "$script" </dev/tty || rc=$?
  else
    NONINTERACTIVE=1 /bin/bash -c "$script" </dev/null || rc=$?
  fi
  # Homebrew stops when its headless Command Line Tools install fails (it needs git).
  # The dialog route still works then: install them, and try Homebrew once more.
  if [ "$rc" != "0" ] && ! clt_ok; then
    install_clt_with_dialog
    rc=0
    if [ "$CAN_ASK" = "1" ]; then
      /bin/bash -c "$script" </dev/tty || rc=$?
    else
      NONINTERACTIVE=1 /bin/bash -c "$script" </dev/null || rc=$?
    fi
  fi
  if [ "$rc" != "0" ]; then
    if [ "$CAN_ASK" != "1" ]; then
      die "Homebrew could not install without a terminal to ask for your password in." \
          "沒有可以輸入密碼的終端機，Homebrew 無法安裝。"
    fi
    die "The Homebrew installer stopped (exit $rc)." "Homebrew 安裝程式中途停止（exit ${rc}）。"
  fi
  use_brew || die "Homebrew reported success but brew is not found." "Homebrew 說裝好了，卻找不到 brew。"
  persist_brew_env
  note "Homebrew is installed. Its \"Next steps\" above are done for you; nothing to type." \
       "Homebrew 裝好了。它上面列的「Next steps」這裡已經幫你做好，不用照著打。"
}

ensure_clt() {
  step "Apple Command Line Tools (git, clang)" "Apple 命令列工具（git、clang）"
  if clt_ok; then note "Already installed." "已經有了。"; return 0; fi
  if [ "$DRY_RUN" = "1" ] && [ "$BREW" = "brew" ]; then
    note "(Homebrew's installer above installs them; if it could not, this step opens Apple's installer window)" \
         "（上面的 Homebrew 安裝程式會一起裝；如果它沒裝成，這一步會打開 Apple 的安裝視窗）"
  fi
  install_clt_with_dialog
}

# good_python <path>: Python 3.10 or newer, and on macOS not one of Apple's own.
# Apple's (Xcode, CLT, /usr/bin, python.org's framework) rewraps itself into
# Python.app at runtime and registers as com.apple.python3 instead of ShellBot, so
# macOS privacy permissions (Accessibility for the ⌃⌥Space hotkey) never stick.
# Homebrew's python is a plain binary that keeps the app's identity.
good_python() {
  local real
  [ -x "$1" ] || have "$1" || return 1
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1 || return 1
  [ "$OS" = "Darwin" ] || return 0
  real="$("$1" -c 'import os, sys; print(os.path.realpath(sys.executable))' 2>/dev/null)" || return 1
  case "$real" in
    */Xcode.app/*|/usr/bin/*|/Library/Developer/*|/Library/Frameworks/Python.framework/*|/System/*) return 1 ;;
  esac
  return 0
}

find_python() {
  local p c prefixes="${SHELLFRAME_BREW_PREFIXES:-/opt/homebrew /usr/local}"
  if [ "$OS" = "Darwin" ]; then
    # The Homebrew in use first, wherever it lives.
    case "$BREW" in */bin/brew) prefixes="$(dirname "$(dirname "$BREW")") $prefixes" ;; esac
    for p in $prefixes; do
      for c in python3 python3.12 python3.13 python3.14 python3.11 python3.10; do
        if good_python "$p/bin/$c"; then printf '%s\n' "$p/bin/$c"; return 0; fi
      done
    done
    return 1
  fi
  for c in python3 python3.12 python3.13 python3.11 python3.10 python3.14; do
    if have "$c" && good_python "$(command -v "$c")"; then command -v "$c"; return 0; fi
  done
  return 1
}

macos_dependencies() {
  install_homebrew
  ensure_clt

  step "Python 3" "Python 3"
  if PYTHON="$(find_python)"; then
    note "Using $PYTHON" "使用 $PYTHON"
  else
    installing "Python 3.12" "ShellBot is written in Python" "ShellBot 是用 Python 寫的"
    hint "Run: brew install python@3.12, then paste the install line again." \
         "請執行 brew install python@3.12，再貼一次安裝指令。"
    run "$BREW" install -q python@3.12 </dev/null
    if [ "$DRY_RUN" = "1" ]; then
      PYTHON="python3.12"
    else
      PYTHON="$(find_python)" || PYTHON="$("$BREW" --prefix python@3.12)/bin/python3.12"
      good_python "$PYTHON" || die "Python 3.12 was installed but cannot be used: $PYTHON" "Python 3.12 裝了卻無法使用：$PYTHON"
    fi
  fi

  step "tmux (keeps your sessions alive when the window closes)" "tmux（關掉視窗後 session 還在）"
  if have tmux || { [ -n "$BREW" ] && [ -x "$(dirname "$BREW")/tmux" ]; }; then
    note "Already installed." "已經有了。"
  else
    installing "tmux" "tabs keep running and keep their scrollback across restarts" "分頁在重開後繼續執行、保留捲動紀錄"
    hint "Run: brew install tmux, then paste the install line again." "請執行 brew install tmux，再貼一次安裝指令。"
    run "$BREW" install -q tmux </dev/null
  fi
}

# ── Linux ───────────────────────────────────────────────────

pkg_installed() {
  case "$PM" in
    apt) dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed" ;;
    dnf) rpm -q "$1" >/dev/null 2>&1 ;;
    pacman) pacman -Q "$1" >/dev/null 2>&1 ;;
  esac
}

has_cjk_font() { have fc-list && [ -n "$(fc-list :lang=zh 2>/dev/null | head -1)" ]; }

linux_dependencies() {
  local missing="" webkit="" entry pkg
  step "System packages" "系統套件"
  if have apt-get; then PM=apt
  elif have dnf; then PM=dnf
  elif have pacman; then PM=pacman
  else
    hint "Install with your package manager: git, python3 (3.10+, with venv), tmux, PyGObject, GTK 3 and WebKit2GTK 4.1; then paste the install line again." \
         "請用你的套件管理工具安裝：git、python3（3.10 以上，含 venv）、tmux、PyGObject、GTK 3、WebKit2GTK 4.1，再貼一次安裝指令。"
    die "No supported package manager (apt, dnf or pacman) was found." "找不到支援的套件管理工具（apt、dnf 或 pacman）。"
  fi

  # name|why (English)|why (Chinese)
  local wanted=()
  case "$PM" in
    apt)
      if pkg_installed gir1.2-webkit2-4.1 || pkg_installed gir1.2-webkit2-4.0; then webkit=""; else webkit="gir1.2-webkit2-4.1"; fi
      wanted=(
        "ca-certificates|to download over HTTPS|下載時驗證 HTTPS 用"
        "curl|to download the AI CLI installers|下載 AI CLI 安裝程式用"
        "git|to download ShellBot and update it|下載和更新 ShellBot 用"
        "python3|ShellBot is written in Python|ShellBot 是用 Python 寫的"
        "python3-venv|keeps ShellBot's Python packages in their own folder|讓 ShellBot 的 Python 套件獨立放一個資料夾"
        "tmux|keeps your sessions alive when the window closes|關掉視窗後 session 還在"
        "python3-gi|lets Python draw the window with GTK|讓 Python 能用 GTK 畫視窗"
        "gir1.2-gtk-3.0|the GTK 3 window toolkit|GTK 3 視窗元件"
      )
      if [ -n "$webkit" ]; then
        wanted+=("$webkit|the web view the app's page runs in|App 介面用的網頁引擎")
      fi
      if [ "$UI_LANG" = "zh" ] && ! has_cjk_font; then
        wanted+=("fonts-noto-cjk|Chinese text in the window|視窗裡的中文字型")
      fi
      ;;
    dnf)
      wanted=(
        "ca-certificates|to download over HTTPS|下載時驗證 HTTPS 用"
        "curl|to download the AI CLI installers|下載 AI CLI 安裝程式用"
        "git|to download ShellBot and update it|下載和更新 ShellBot 用"
        "python3|ShellBot is written in Python|ShellBot 是用 Python 寫的"
        "tmux|keeps your sessions alive when the window closes|關掉視窗後 session 還在"
        "python3-gobject|lets Python draw the window with GTK|讓 Python 能用 GTK 畫視窗"
        "gtk3|the GTK 3 window toolkit|GTK 3 視窗元件"
        "webkit2gtk4.1|the web view the app's page runs in|App 介面用的網頁引擎"
      )
      ;;
    pacman)
      wanted=(
        "ca-certificates|to download over HTTPS|下載時驗證 HTTPS 用"
        "curl|to download the AI CLI installers|下載 AI CLI 安裝程式用"
        "git|to download ShellBot and update it|下載和更新 ShellBot 用"
        "python|ShellBot is written in Python|ShellBot 是用 Python 寫的"
        "tmux|keeps your sessions alive when the window closes|關掉視窗後 session 還在"
        "python-gobject|lets Python draw the window with GTK|讓 Python 能用 GTK 畫視窗"
        "gtk3|the GTK 3 window toolkit|GTK 3 視窗元件"
        "webkit2gtk-4.1|the web view the app's page runs in|App 介面用的網頁引擎"
      )
      ;;
  esac

  for entry in "${wanted[@]}"; do
    [ -n "$entry" ] || continue
    pkg="${entry%%|*}"
    if pkg_installed "$pkg"; then continue; fi
    entry="${entry#*|}"
    installing "$pkg" "${entry%%|*}" "${entry#*|}"
    missing="$missing $pkg"
  done

  if [ -z "$missing" ]; then
    note "Everything is already installed." "需要的都已經裝好了。"
  else
    case "$PM" in
      apt) hint "Run: sudo apt-get update && sudo apt-get install -y$missing  then paste the install line again." \
                "請執行 sudo apt-get update && sudo apt-get install -y$missing ，再貼一次安裝指令。" ;;
      dnf) hint "Run: sudo dnf install -y$missing  then paste the install line again." \
                "請執行 sudo dnf install -y$missing ，再貼一次安裝指令。" ;;
      pacman) hint "Run: sudo pacman -S --needed$missing  then paste the install line again." \
                   "請執行 sudo pacman -S --needed$missing ，再貼一次安裝指令。" ;;
    esac
    get_root
    # shellcheck disable=SC2086  # $missing is a list of package names
    case "$PM" in
      apt)
        quiet_root_run env DEBIAN_FRONTEND=noninteractive apt-get update </dev/null
        # Older releases (Debian 11, Ubuntu 20.04) only have WebKit2GTK 4.0.
        if [ -n "$webkit" ] && [ "$DRY_RUN" != "1" ] && ! apt-cache show gir1.2-webkit2-4.1 >/dev/null 2>&1; then
          missing="${missing/gir1.2-webkit2-4.1/gir1.2-webkit2-4.0}"
        fi
        quiet_root_run env DEBIAN_FRONTEND=noninteractive apt-get install -y $missing </dev/null ;;
      dnf) quiet_root_run dnf install -y $missing </dev/null ;;
      pacman) quiet_root_run pacman -S --needed --noconfirm $missing </dev/null ;;
    esac
  fi

  step "Python 3" "Python 3"
  hint "ShellBot needs Python 3.10 or newer as python3. Install it with your package manager, then paste the install line again." \
       "ShellBot 需要 Python 3.10 以上（python3）。請用套件管理工具安裝，再貼一次安裝指令。"
  if PYTHON="$(find_python)"; then
    note "Using $PYTHON" "使用 $PYTHON"
  elif [ "$DRY_RUN" = "1" ]; then
    PYTHON="python3"
  else
    die "No Python 3.10 or newer was found." "找不到 Python 3.10 以上的版本。"
  fi
}

# Root for the package manager: nothing as root, sudo otherwise. sudo asks for the
# password on the terminal itself; with nobody to ask, it may only use a cached one.
# get_root soft: return 1 instead of stopping the install (for optional steps).
get_root() {
  local soft="${1:-}"
  if [ "$(id -u)" = "0" ]; then ROOT_CMD=""; return 0; fi
  if ! have sudo; then
    [ "$soft" = "soft" ] && return 1
    die "Installing system packages needs root, and sudo is not installed. Run this line as root, or install the packages above as root." \
        "安裝系統套件需要 root 權限，但這台沒有 sudo。請用 root 執行這行指令，或用 root 先裝好上面列的套件。"
  fi
  if [ "$CAN_ASK" = "1" ]; then
    ROOT_CMD="sudo"
    say "    sudo may now ask for your password (the one you log in with). Nothing shows while you type; press Enter when done." \
        "    接下來 sudo 可能會問你的密碼（登入用的那組）。打字時畫面不會顯示，輸入完按 Enter。"
    return 0
  fi
  ROOT_CMD="sudo -n"
  if [ "$DRY_RUN" != "1" ] && ! sudo -n true 2>/dev/null; then
    [ "$soft" = "soft" ] && return 1
    die "sudo needs a password and there is no terminal to type it in." "sudo 需要密碼，但沒有可以輸入密碼的終端機。"
  fi
}

# ── ShellBot itself ─────────────────────────────────────────

fetch_source() {
  local cur backup
  step "ShellBot ($REF)" "ShellBot 本體（${REF}）"
  hint "Check the network, then paste the install line again. To start over, move $INSTALL_DIR aside first." \
       "請確認網路後再貼一次安裝指令。若要重新開始，先把 $INSTALL_DIR 移到別處。"
  if [ -d "$INSTALL_DIR/.git" ]; then
    say "    Updating the existing install..." "    更新已經裝好的版本…"
    cd "$INSTALL_DIR"
    # Local edits are stashed so the update never blocks on them.
    if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
      git stash push -u -m "install.sh-auto-$(date +%s)" >/dev/null 2>&1 || true
    fi
    if [ -z "${SHELLFRAME_REF:-}" ]; then
      # As before: fast-forward the branch it is on; if history diverged, follow origin/main.
      if ! git pull --ff-only </dev/null 2>/dev/null; then
        note "Fast-forward was not possible; syncing to origin/main" "無法快轉，改為同步到 origin/main"
        LAST_CMD="git fetch origin main && git reset --hard origin/main"
        git fetch origin main </dev/null
        git reset --hard origin/main
      fi
    else
      cur="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
      LAST_CMD="git fetch origin $REF"
      git fetch origin "$REF" </dev/null
      if [ "$cur" = "$REF" ]; then
        git reset --hard "origin/$REF"
      else
        git checkout -B "$REF" "origin/$REF"
      fi
    fi
  else
    FRESH=1
    if [ -d "$INSTALL_DIR" ] && [ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null)" ]; then
      # Files but no .git: a downloaded zip or a copied tree. Do not `git init` in place
      # (untracked bundles and launchers would survive and mix versions); keep a backup.
      backup="${INSTALL_DIR}.non-git-backup.$(date +%Y%m%d%H%M%S)"
      note "Backing up the existing folder to $backup" "把原本的資料夾備份到 $backup"
      mv "$INSTALL_DIR" "$backup"
      FRESH=0
    fi
    mkdir -p "$(dirname "$INSTALL_DIR")"
    say "    Downloading ShellBot..." "    下載 ShellBot…"
    LAST_CMD="git clone --branch $REF $REPO_URL $INSTALL_DIR"
    git clone --branch "$REF" "$REPO_URL" "$INSTALL_DIR" </dev/null
  fi
  cd "$INSTALL_DIR"
}

setup_venv() {
  local current_real
  step "ShellBot's Python packages" "ShellBot 的 Python 套件"
  hint "Check the network, then paste the install line again. If it keeps failing, delete $INSTALL_DIR/.venv and run it again." \
       "請確認網路後再貼一次安裝指令。若一直失敗，刪掉 $INSTALL_DIR/.venv 再跑一次。"
  cd "$INSTALL_DIR"
  if [ -d ".venv" ]; then
    current_real="$(.venv/bin/python -c 'import os, sys; print(os.path.realpath(sys.executable))' 2>/dev/null || true)"
    if [ -z "$current_real" ]; then
      # Its Python was removed or upgraded away: the venv cannot run at all.
      note "The existing .venv no longer runs; rebuilding it" "原本的 .venv 已經無法執行，重建"
      mv .venv ".venv.broken-bak.$(date +%s)" 2>/dev/null || rm -rf .venv
    elif [ "$OS" = "Darwin" ]; then
      case "$current_real" in
        */Xcode.app/*|/Library/Developer/*|/Library/Frameworks/Python.framework/*|/System/Library/Frameworks/Python.framework/*)
          note "The existing .venv uses Apple's Python; rebuilding it with Homebrew's" "原本的 .venv 用的是 Apple 的 Python，改用 Homebrew 的重建"
          mv .venv ".venv.applepython-bak.$(date +%s)" 2>/dev/null || rm -rf .venv
          ;;
      esac
    fi
  fi
  if [ ! -d ".venv" ]; then
    say "    Creating the virtual environment with $PYTHON..." "    用 $PYTHON 建立虛擬環境…"
    if [ "$OS" = "Linux" ]; then
      # The GTK bindings (python3-gi) come from the distribution, not pip.
      LAST_CMD="$PYTHON -m venv --system-site-packages .venv"
      "$PYTHON" -m venv --system-site-packages .venv
    else
      LAST_CMD="$PYTHON -m venv .venv"
      "$PYTHON" -m venv .venv
    fi
  elif [ "$OS" = "Linux" ] && ! .venv/bin/python -c 'import gi' >/dev/null 2>&1 \
       && "$PYTHON" -c 'import gi' >/dev/null 2>&1; then
    # An older install made the venv without the system's packages, so it could not
    # see python3-gi and the window never opened. Let it see them.
    note "Letting the existing .venv use the system's GTK bindings" "讓原本的 .venv 也能用系統的 GTK 套件"
    sed -i 's/^include-system-site-packages = false/include-system-site-packages = true/' .venv/pyvenv.cfg
  fi
  say "    Installing Python packages (pywebview, pyte)..." "    安裝 Python 套件（pywebview、pyte）…"
  LAST_CMD=".venv/bin/pip install -q -r requirements.txt"
  .venv/bin/pip install -q -r requirements.txt </dev/null
}

verify_imports() {
  local code="import webview, pyte"
  step "Checking that ShellBot can open its window" "確認 ShellBot 開得了視窗"
  case "$OS" in
    Darwin) code="$code
import objc, AppKit, WebKit" ;;
    Linux) code="$code
import gi
gi.require_version('Gtk', '3.0')
try:
    gi.require_version('WebKit2', '4.1')
except ValueError:
    gi.require_version('WebKit2', '4.0')
from gi.repository import WebKit2" ;;
  esac
  hint "Run: $VENV_PY -c \"import webview\" to see the error. On Linux, check that python3-gi and the WebKit2GTK gir package are installed." \
       "請執行 $VENV_PY -c \"import webview\" 看錯誤訊息。Linux 請確認 python3-gi 和 WebKit2GTK 的 gir 套件已安裝。"
  LAST_CMD="$VENV_PY -c '$code'"
  "$VENV_PY" -c "$code" </dev/null
  note "OK" "沒問題"
}

write_launchers() {
  step "Commands: shellframe and sfctl" "指令：shellframe 與 sfctl"
  mkdir -p "$BIN_DIR"

  # shellframe — main GUI app
  cat > "$BIN_DIR/shellframe" << 'LAUNCHER'
#!/bin/bash
if [ -f "$HOME/.zprofile" ]; then source "$HOME/.zprofile" 2>/dev/null; fi
if [ -f "$HOME/.zshrc" ]; then source "$HOME/.zshrc" 2>/dev/null; fi
cd ~/.local/apps/shellframe
PY_LAUNCHER=".venv/bin/python"
if [ "$(uname)" = "Darwin" ]; then
  REAL_PY="$("$PY_LAUNCHER" -c 'import os,sys; print(os.path.realpath(sys.executable))' 2>/dev/null || true)"
  case "$REAL_PY" in
    */Frameworks/Python.framework/Versions/*/bin/python*)
      PY_APP="${REAL_PY%/bin/python*}/Resources/Python.app/Contents/MacOS/Python"
      if [ -x "$PY_APP" ]; then
        ln -sf "$PY_APP" ".venv/bin/ShellFrame"
        PY_LAUNCHER=".venv/bin/ShellFrame"
      fi
      ;;
  esac
fi
# Force arm64 on Apple Silicon so the python child doesn't inherit a
# Rosetta parent shell — see ShellFrame.app launcher for full rationale.
if [[ "$(sysctl -in hw.optional.arm64 2>/dev/null)" == "1" ]]; then
  exec arch -arm64 "$PY_LAUNCHER" main.py "$@"
fi
exec "$PY_LAUNCHER" main.py "$@"
LAUNCHER
  chmod +x "$BIN_DIR/shellframe"

  # sfctl — remote control for AI agents
  cat > "$BIN_DIR/sfctl" << 'SFCTL'
#!/bin/bash
exec ~/.local/apps/shellframe/.venv/bin/python ~/.local/apps/shellframe/sfctl.py "$@"
SFCTL
  chmod +x "$BIN_DIR/sfctl"
  note "$BIN_DIR/shellframe, $BIN_DIR/sfctl" "$BIN_DIR/shellframe、$BIN_DIR/sfctl"
}

# macOS .app (Spotlight + Launchpad + Finder)
install_mac_app() {
  step "ShellBot.app" "ShellBot.app"
  # Prefer /Applications (Launchpad visible), fall back to ~/Applications. Installed as
  # ShellBot.app: Finder, Launchpad, the Dock and ⌘-Tab show the bundle's file name. The
  # template keeps the name ShellFrame.app (app_bundle.py says why).
  if [ -w /Applications ] || [ -w /Applications/ShellBot.app ]; then
    APP_DEST="/Applications/ShellBot.app"
  else
    APP_DEST="${HOME}/Applications/ShellBot.app"
    mkdir -p ~/Applications
  fi

  # Copy .app bundle (not symlink — Spotlight/Launchpad ignore symlinks to dot-folders)
  rm -rf "$APP_DEST"
  cp -R "$INSTALL_DIR/ShellFrame.app" "$APP_DEST"

  mkdir -p "$APP_DEST/Contents/Resources"

  # The app template historically kept the shell payload at
  # Contents/MacOS/shellframe. For LaunchServices/Dock identity, the executable
  # must be a real Mach-O process that stays alive while Python runs as a child.
  if [ ! -f "$APP_DEST/Contents/Resources/shellframe.sh" ]; then
    cp "$INSTALL_DIR/ShellFrame.app/Contents/MacOS/shellframe" "$APP_DEST/Contents/Resources/shellframe.sh"
  fi
  if [ -f "$APP_DEST/Contents/MacOS/shellframe.sh" ]; then
    mv "$APP_DEST/Contents/MacOS/shellframe.sh" "$APP_DEST/Contents/Resources/shellframe.sh"
  fi
  if [ -f "$INSTALL_DIR/scripts/macos_app_launcher.c" ] && have clang; then
    LAUNCHER_ARCH_FLAGS=()
    case "$(uname -m)" in
      arm64) LAUNCHER_ARCH_FLAGS=(-arch arm64) ;;
      x86_64) LAUNCHER_ARCH_FLAGS=(-arch x86_64) ;;
    esac
    clang "${LAUNCHER_ARCH_FLAGS[@]}" -mmacosx-version-min=12.0 \
      "$INSTALL_DIR/scripts/macos_app_launcher.c" \
      -o "$APP_DEST/Contents/MacOS/shellframe"
  else
    warn "clang not found; ShellBot.app will use the shell launcher fallback." "找不到 clang；ShellBot.app 會改用 shell 啟動器。"
  fi
  chmod 755 "$APP_DEST/Contents/MacOS/shellframe" 2>/dev/null || true
  chmod 644 "$APP_DEST/Contents/Resources/shellframe.sh" 2>/dev/null || true
  xattr -cr "$APP_DEST" 2>/dev/null || true

  # Stamp Info.plist with current version from version.json
  CURRENT_VER=$("$VENV_PY" -c "import json; print(json.load(open('$INSTALL_DIR/version.json'))['version'])" 2>/dev/null || echo "0.0.0")
  PLIST="$APP_DEST/Contents/Info.plist"
  if [ -f "$PLIST" ]; then
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $CURRENT_VER" "$PLIST" 2>/dev/null || true
    /usr/libexec/PlistBuddy -c "Set :CFBundleVersion $CURRENT_VER" "$PLIST" 2>/dev/null || true
  fi

  # Clean up other copies of this app: an older install's ShellFrame.app, and the
  # ~/Applications one when we installed to /Applications (this bundle id only).
  for OLD_APP in "/Applications/ShellFrame.app" "${HOME}/Applications/ShellFrame.app" "${HOME}/Applications/ShellBot.app"; do
    [ "$OLD_APP" = "$APP_DEST" ] && continue
    [ -d "$OLD_APP" ] && [ ! -L "$OLD_APP" ] || continue
    if [ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$OLD_APP/Contents/Info.plist" 2>/dev/null)" = "com.h2ocloud.shellframe" ]; then
      rm -rf "$OLD_APP" 2>/dev/null || true
    fi
  done

  # Ad-hoc code sign. Avoid --deep: it can stamp shell payloads as nested code
  # and leave LaunchServices with a broken bundle cache.
  codesign --force --sign - "$APP_DEST/Contents/MacOS/shellframe" 2>/dev/null || true
  codesign --force --sign - "$APP_DEST" 2>/dev/null || true

  # Register with Launch Services for Spotlight indexing, and clear stale
  # registrations of this app that commonly survive failed installs or its old name.
  LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
  "$LSREGISTER" -u "${HOME}/.Trash/ShellFrame.app" 2>/dev/null || true
  for OLD_APP in "/Applications/ShellFrame.app" "${HOME}/Applications/ShellFrame.app" "/Applications/ShellBot.app" "${HOME}/Applications/ShellBot.app"; do
    [ "$OLD_APP" = "$APP_DEST" ] || [ -e "$OLD_APP" ] || "$LSREGISTER" -u "$OLD_APP" 2>/dev/null || true
  done
  "$LSREGISTER" -f "$APP_DEST" 2>/dev/null || true

  # Keep ShellBot pinned first in the Dock by default. Not by writing the Dock's plist and
  # restarting it: the preferences daemon holds that domain, and a Dock with changes it has
  # not saved writes them back as it quits, so such a pin can be lost without a word.
  # app_bundle.pin_dock() goes through `defaults`, restarts the Dock, reads the pin back and
  # tries again if it is gone. Idempotent (a pin already first and right changes nothing, and
  # the Dock is not restarted); best effort, it never fails the install; can be skipped for
  # scripted installs with SHELLFRAME_SKIP_DOCK=1.
  if [ "${SHELLFRAME_SKIP_DOCK:-0}" != "1" ]; then
    INSTALL_DIR="$INSTALL_DIR" APP_DEST="$APP_DEST" "$VENV_PY" - <<'PY' 2>/dev/null || true
import os
import sys

sys.path.insert(0, os.environ["INSTALL_DIR"])
import app_bundle

app_bundle.pin_dock(os.environ["APP_DEST"], log=lambda m: print("  Dock: " + m))
PY
  fi

  note "App: $APP_DEST" "App：$APP_DEST"
}

# Linux: an entry in the desktop's app menu.
install_linux_desktop_entry() {
  local dir="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
  step "App menu entry" "應用程式選單項目"
  mkdir -p "$dir"
  cat > "$dir/shellbot.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=ShellBot
Comment=A home for your AI bots
Exec=$BIN_DIR/shellframe
Icon=$INSTALL_DIR/icon.png
Terminal=false
Categories=Development;
DESKTOP
  note "$dir/shellbot.desktop" "$dir/shellbot.desktop"
}

ensure_path_rc() {
  local shell_rc=""
  case "$(basename "${SHELL:-zsh}")" in
    zsh)  shell_rc="$HOME/.zshrc" ;;
    bash) shell_rc="$HOME/.bashrc" ;;
    fish) shell_rc="$HOME/.config/fish/config.fish" ;;
  esac
  if [ -n "$shell_rc" ] && ! grep -q '.local/bin' "$shell_rc" 2>/dev/null; then
    mkdir -p "$(dirname "$shell_rc")"
    # shellcheck disable=SC2016  # written literally, expanded by the shell that reads it
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$shell_rc"
    note "Added ~/.local/bin to PATH in $(basename "$shell_rc")" "已把 ~/.local/bin 加進 $(basename "$shell_rc") 的 PATH"
  fi
}

# ── AI CLIs (optional) ──────────────────────────────────────

# The install command for a CLI comes from the provider registry (usage_probe), the
# same one the app's AI accounts panel uses. Before the source is here (dry run) the
# registry cannot be read; a dry run only prints anyway.
registry_cmd() {
  [ -x "$VENV_PY" ] || return 1
  "$VENV_PY" - "$INSTALL_DIR" "$1" <<'PY' 2>/dev/null
import sys
sys.path.insert(0, sys.argv[1])
import usage_probe
print((usage_probe.PROVIDER_SPECS[sys.argv[2]].get("install") or {}).get("command") or "")
PY
}

cli_present() {
  case "$1" in
    claude) have claude || [ -x "$HOME/.local/bin/claude" ] || [ -x "$HOME/.claude/local/claude" ] ;;
    codex) have codex ;;
  esac
}

ensure_node() {
  local major
  if have npm; then return 0; fi
  installing "Node.js" "Codex is installed with npm" "Codex 要用 npm 安裝"
  case "$OS" in
    Darwin) run "$BREW" install -q node </dev/null ;;
    Linux)
      get_root soft || return 1
      case "$PM" in
        apt) quiet_root_run env DEBIAN_FRONTEND=noninteractive apt-get install -y nodejs npm </dev/null ;;
        dnf) quiet_root_run dnf install -y nodejs npm </dev/null ;;
        pacman) quiet_root_run pacman -S --needed --noconfirm nodejs npm </dev/null ;;
      esac ;;
  esac
  [ "$DRY_RUN" = "1" ] && return 0
  have npm || return 1
  major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || echo 0)"
  if [ "$major" -lt 18 ]; then
    warn "This system's Node.js is $major; Codex needs 18 or newer (see https://nodejs.org)." \
         "這台的 Node.js 是 $major 版；Codex 需要 18 以上（見 https://nodejs.org）。"
    return 1
  fi
}

install_cli() {
  local name="$1" label cmd fallback
  case "$name" in
    claude) label="Claude Code"; fallback="curl -fsSL https://claude.ai/install.sh | bash" ;;
    codex) label="Codex"; fallback="npm install -g @openai/codex" ;;
    *) warn "Unknown AI CLI: $name" "不認得的 AI CLI：$name"; return 0 ;;
  esac
  if cli_present "$name"; then note "$label is already installed." "$label 已經裝好了。"; return 0; fi
  cmd="$(registry_cmd "$name" || true)"
  [ -n "$cmd" ] || cmd="$fallback"
  installing "$label" "optional: an AI CLI to run in a ShellBot tab" "選用：在 ShellBot 分頁裡跑的 AI CLI"
  if [ "$name" = "codex" ] && ! ensure_node; then
    warn "Codex was not installed. Later: install Node.js 18+, then run: $cmd" \
         "Codex 沒有裝。之後可以先裝 Node.js 18 以上，再執行：$cmd"
    return 0
  fi
  # A global npm install goes to ~/.local on Linux, so it needs no sudo.
  if [ "$name" = "codex" ] && [ "$OS" = "Linux" ]; then
    if ! run env NPM_CONFIG_PREFIX="$HOME/.local" bash -c "$cmd" </dev/null; then
      warn "$label did not install. Run it yourself later: $cmd" "$label 沒有裝成功。之後可以自己執行：$cmd"
      return 0
    fi
  elif ! run bash -c "$cmd" </dev/null; then
    warn "$label did not install. Run it yourself later: $cmd" "$label 沒有裝成功。之後可以自己執行：$cmd"
    return 0
  fi
  case "$name" in
    claude) NEXT="${NEXT}$(t "  Claude Code: in ShellBot press + and open a Claude Code tab; the first time it opens your browser to log in." \
                             "  Claude Code：在 ShellBot 按「+」開一個 Claude Code 分頁，第一次會開瀏覽器請你登入。")
" ;;
    codex) NEXT="${NEXT}$(t "  Codex: open a Codex tab and choose \"Sign in with ChatGPT\" the first time." \
                            "  Codex：開一個 Codex 分頁，第一次選「Sign in with ChatGPT」登入。")
" ;;
  esac
}

offer_ai_clis() {
  local want="${SHELLFRAME_INSTALL_CLIS:-}" name
  step "AI CLIs (optional)" "AI CLI（選用）"
  say "    ShellBot runs the AI CLIs you use (Claude Code, Codex, ...) in its tabs. None is needed to install ShellBot." \
      "    ShellBot 是在分頁裡跑你用的 AI CLI（Claude Code、Codex…），安裝 ShellBot 本身不需要它們。"
  if [ -z "$want" ]; then
    if [ "$CAN_ASK" = "1" ]; then
      if cli_present claude; then note "Claude Code is already installed." "Claude Code 已經裝好了。"
      elif ask "Also install Claude Code now? [y/N]" "要順便安裝 Claude Code 嗎？[y/N]"; then want="claude"; fi
      if cli_present codex; then note "Codex is already installed." "Codex 已經裝好了。"
      elif ask "Also install Codex (OpenAI) now? [y/N]" "要順便安裝 Codex（OpenAI）嗎？[y/N]"; then want="${want:+$want,}codex"; fi
    else
      note "No terminal to ask in, so none is installed. Add them later from ShellBot's AI accounts panel." \
           "沒有可以回答問題的終端機，先不裝。之後可以在 ShellBot 的「AI 帳號」面板安裝。"
    fi
  fi
  [ "$want" = "none" ] && want=""
  for name in $(printf '%s' "$want" | tr ',' ' '); do
    install_cli "$name"
  done
}

# What is installed and what is not, from the same registry the app uses.
report_ai_clis() {
  [ -x "$VENV_PY" ] || return 0
  echo ""
  "$VENV_PY" - "$INSTALL_DIR" <<'PY' || echo "  (could not list them; see the AI 帳號 panel)"
import sys
sys.path.insert(0, sys.argv[1])
import usage_probe as u
missing = []
for name, spec in u.PROVIDER_SPECS.items():
    ok = u.provider_installed(name)
    print(("  ✅ " if ok else "  ⬜ ") + spec["label"] + ("" if ok else " — not installed"))
    cmd = (spec.get("install") or {}).get("command")
    if not ok and cmd:
        missing.append("      " + cmd)
if missing:
    print("\n  To install the missing ones (one is enough to start):")
    print("\n".join(missing))
    print("      (or use the ⬜ entries in ShellBot's AI 帳號 panel)")
PY
}

# ── Finish ──────────────────────────────────────────────────

launch_app() {
  [ "$FRESH" = "1" ] || return 0
  [ "${SHELLFRAME_NO_LAUNCH:-0}" = "1" ] && return 0
  [ "$DRY_RUN" = "1" ] && return 0
  [ -t 1 ] || return 0
  case "$OS" in
    Darwin)
      say "Opening ShellBot..." "正在打開 ShellBot…"
      open "$APP_DEST" 2>/dev/null || true ;;
    Linux)
      if [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]; then
        say "Opening ShellBot..." "正在打開 ShellBot…"
        (cd "$HOME" && nohup "$BIN_DIR/shellframe" >/dev/null 2>&1 &) || true
      fi ;;
  esac
}

summary() {
  local version="?" secs
  secs=$(( $(date +%s) - START_TS ))
  [ -x "$VENV_PY" ] && version="$("$VENV_PY" -c "import json; print(json.load(open('$INSTALL_DIR/version.json'))['version'])" 2>/dev/null || echo '?')"
  echo ""
  if [ "$DRY_RUN" = "1" ]; then
    say "Dry run finished: nothing was installed or changed." "試跑結束：什麼都沒有安裝或修改。"
    [ -n "$INSTALLED" ] && say "Would install: $INSTALLED" "會安裝：$INSTALLED"
    return 0
  fi
  say "✅ ShellBot v${version} installed! (took $(( secs / 60 ))m $(( secs % 60 ))s)" \
      "✅ ShellBot v${version} 裝好了！（花了 $(( secs / 60 )) 分 $(( secs % 60 )) 秒）"
  [ -n "$INSTALLED" ] && say "   Installed this time: $INSTALLED" "   這次安裝了：$INSTALLED"
  echo ""
  case "$OS" in
    Darwin) say "  Open it: ShellBot in Launchpad or Spotlight, or type shellframe in a new terminal window." \
                "  打開方式：Launchpad 或 Spotlight 找 ShellBot，或在新的終端機視窗輸入 shellframe。" ;;
    *) say "  Open it: ShellBot in the app menu, or type shellframe in a new terminal window." \
           "  打開方式：應用程式選單裡的 ShellBot，或在新的終端機視窗輸入 shellframe。" ;;
  esac
  if [ -n "$NEXT" ]; then
    say "  Next:" "  下一步："
    printf '%s' "$NEXT"
  fi
  if [ "$OS" = "Darwin" ]; then
    echo ""
    say "  ⚙️  Run \`sfctl permissions\` once to pre-grant macOS Privacy + firewall access; it avoids permission popups later." \
        "  ⚙️  建議跑一次 \`sfctl permissions\`，先把 macOS 隱私權限與防火牆設好，之後比較不會跳權限視窗。"
  fi
  echo ""
}

main() {
  detect_lang
  detect_tty
  say "Installing ShellBot..." "安裝 ShellBot…"
  [ "$DRY_RUN" = "1" ] && say "(dry run: checks only; nothing will be installed or changed)" "（試跑：只檢查，不會安裝或修改任何東西）"
  case "$OS" in
    Darwin) macos_dependencies ;;
    Linux) linux_dependencies ;;
    *) die "This installer is for macOS and Linux. On Windows use install.ps1 in PowerShell." \
           "這個安裝程式是給 macOS 和 Linux 的。Windows 請在 PowerShell 用 install.ps1。" ;;
  esac

  if [ "$DRY_RUN" = "1" ]; then
    step "ShellBot itself" "ShellBot 本體"
    if [ -d "$INSTALL_DIR/.git" ]; then
      note "[dry-run] would update $INSTALL_DIR to $REF" "[dry-run] 會把 $INSTALL_DIR 更新到 $REF"
    else
      note "[dry-run] would clone $REPO_URL ($REF) into $INSTALL_DIR" "[dry-run] 會把 ${REPO_URL}（${REF}）下載到 $INSTALL_DIR"
    fi
    note "[dry-run] would create $INSTALL_DIR/.venv with $PYTHON and pip install -r requirements.txt" \
         "[dry-run] 會用 $PYTHON 建立 $INSTALL_DIR/.venv，並 pip install -r requirements.txt"
    note "[dry-run] would write $BIN_DIR/shellframe and $BIN_DIR/sfctl" "[dry-run] 會寫入 $BIN_DIR/shellframe 與 $BIN_DIR/sfctl"
    [ "$OS" = "Darwin" ] && note "[dry-run] would install ShellBot.app" "[dry-run] 會安裝 ShellBot.app"
  else
    fetch_source
    setup_venv
    verify_imports
    write_launchers
    case "$OS" in
      Darwin) install_mac_app ;;
      Linux) install_linux_desktop_entry ;;
    esac
    ensure_path_rc
  fi

  offer_ai_clis
  report_ai_clis
  summary
  launch_app
  DONE=1
}

main "$@"
