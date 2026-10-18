"""沒有 ShellFrame 的主機，用 SSH 跑 headless cursor-agent。

這不是第二條 transport。呼叫端給 runner(argv)；本模組匯入時不開連線。
正式環境預設 plan／ask，寫入要另行授權，而且一律不准重啟服務。
"""

import re

_TARGET_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,200}$")
_RESTART_RE = re.compile(
    r"(systemctl|service)\s+(restart|stop|reload)|"
    r"\b(apachectl|apache2ctl|iisreset)\b|"
    r"\bnet\s+stop\b|"
    r"\brestart\s+(apache|httpd|nginx)\b|"
    r"重啟\s*(apache|httpd|nginx|服務)|"
    r"(停用|關掉|關閉)\s*(apache|服務)",
    re.I)
_MODEL_OK = ("composer", "grok-")
_WRITE_NOTE = (
    "Do not restart or stop services. "
    "Apache, httpd, nginx, systemctl restart/stop/reload, iisreset, "
    "and net stop are forbidden.\n"
)


def normalize(name, spec) -> dict:
    """把一筆設定收成封閉的 worker。正式環境不能把 mode 設成 agent。"""
    spec = spec if isinstance(spec, dict) else {}
    production = bool(spec.get("production"))
    mode = str(spec.get("mode") or "").strip().lower()
    if mode not in ("ask", "plan", "agent"):
        mode = "plan" if production else "agent"
    if production and mode == "agent":
        mode = "plan"
    shell = str(spec.get("shell") or "posix").strip().lower()
    if shell not in ("posix", "powershell"):
        shell = "posix"
    model = str(spec.get("model") or "composer-2.5").strip()
    if not model.startswith(_MODEL_OK):
        model = "composer-2.5"
    return {
        "name": str(name or "").strip(),
        "target": str(spec.get("target") or "").strip(),
        "user": str(spec.get("user") or "").strip(),
        "shell": shell,
        "production": production,
        "mode": mode,
        "model": model,
        "workspace": str(spec.get("workspace") or "").strip(),
    }


def normalize_all(raw) -> dict:
    out = {}
    if not isinstance(raw, dict):
        return out
    for name, spec in raw.items():
        item = normalize(name, spec)
        if item["name"] and item["target"]:
            out[item["name"]] = item
    return out


def destination(worker) -> str:
    user = (worker or {}).get("user") or ""
    target = (worker or {}).get("target") or ""
    return f"{user}@{target}" if user else target


def gate(worker, task, allow_write=False):
    """(mode, error)。mode 為 agent 表示不加 --mode。error 非空就不要執行。"""
    dest = destination(worker or {})
    if not _TARGET_RE.fullmatch(dest):
        return "", "bad worker target"
    if _RESTART_RE.search(task or ""):
        return "", "refusing to restart a service"
    if (worker or {}).get("production"):
        if allow_write:
            return "agent", ""
        mode = (worker or {}).get("mode") or "plan"
        return mode if mode in ("ask", "plan") else "plan", ""
    if allow_write:
        return "agent", ""
    return (worker or {}).get("mode") or "agent", ""


def _quote(shell, text) -> str:
    if shell == "powershell":
        return "'" + (text or "").replace("'", "''") + "'"
    return "'" + (text or "").replace("'", "'\"'\"'") + "'"


def _cmd_safe(text) -> str:
    """cursor-agent 在 Windows 是 .cmd shim，PowerShell 之後 cmd.exe 會再解析一次：
    `"` 會提早結束引號（`&`、`|` 變成指令）、`%VAR%` 會展開、換行會截斷引數。"""
    text = re.sub(r"[\r\n]+", " ", text or "")
    return text.replace('"', "'").replace("%", "\uff05")


def argv(worker, task, mode) -> list:
    """本地行程用的 argv。沒有 shell。遠端那一格已把任務包成一個引數。"""
    shell = (worker or {}).get("shell") or "posix"
    body = task or ""
    if (worker or {}).get("production") and mode == "agent":
        body = _WRITE_NOTE + body
    parts = ["cursor-agent", "-p", "--trust", "--output-format", "text",
             "--model", (worker or {}).get("model") or "composer-2.5"]
    if mode in ("ask", "plan"):
        parts.extend(["--mode", mode])
    elif not (worker or {}).get("production"):
        parts.append("--force")
    workspace = (worker or {}).get("workspace") or ""
    if shell == "powershell":
        body = _cmd_safe(body)
        # Windows PowerShell 5.1 只在引數含空白時才加雙引號；結尾反斜線會吃掉收尾引號。
        if not re.search(r"\s", body) or body.endswith("\\"):
            body += " "
        workspace = _cmd_safe(workspace).rstrip("\\")
    if workspace:
        parts.extend(["--workspace", _quote(shell, workspace)])
    parts.append(_quote(shell, body))
    remote = " ".join(parts)
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15",
            destination(worker), remote]


def execute(worker, task, *, allow_write=False, runner=None) -> dict:
    """runner(argv) -> {code, out, err}。沒給 runner 就不連線。"""
    mode, err = gate(worker, task, allow_write=allow_write)
    if err:
        return {"ok": False, "message": err, "argv": [], "mode": mode, "out": ""}
    built = argv(worker, task, mode)
    if runner is None:
        return {"ok": False, "message": "no ssh runner", "argv": built,
                "mode": mode, "out": ""}
    try:
        result = runner(built) or {}
    except Exception:
        return {"ok": False, "message": "ssh failed", "argv": built,
                "mode": mode, "out": ""}
    if int(result.get("code") or 0) != 0:
        err_text = (result.get("err") or "ssh failed").strip()
        return {"ok": False, "message": err_text[:200], "argv": built,
                "mode": mode, "out": ""}
    return {"ok": True, "message": "", "argv": built, "mode": mode,
            "out": result.get("out") or ""}


def default_runner(argv, timeout=180):
    """真的執行。測試不要呼叫這支。"""
    import subprocess
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"code": 124, "out": "", "err": f"ssh timed out after {timeout}s"}
    except FileNotFoundError:
        return {"code": 127, "out": "", "err": "ssh not found"}
    return {"code": proc.returncode, "out": proc.stdout or "", "err": proc.stderr or ""}
