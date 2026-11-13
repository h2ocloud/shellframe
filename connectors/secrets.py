"""Where a connected service's key or token lives, and how any text is masked before it is shown or logged.

  * A secret goes to the system's key store (macOS Keychain through `security`, Windows Credential
    Manager through advapi32), never to config.json, connectors.json, a log, a status or the screen.
    A platform without one says so (`Unsupported`); nothing is kept in a file as a fallback.
  * `mask()` is the one door every log line, error message and shown command goes through. It knows the
    secrets this process handled (`remember`) and the shapes tokens come in.
  * A test never reaches the real key store: with SHELLFRAME_TESTS set, the default backend is a
    refusing one, and a test passes `MemoryBackend()` (or a fake `security` program) in.

The macOS path writes through `security -i` (commands on stdin), so the value is not in the argument
list (`ps` shows arguments) and is read back with `find-generic-password -w` (stdout). Written and
exercised against a fake `security` program only; never run against a real key store by the tests.
The Windows path is written, not run on Windows yet.
"""

import json
import os
import re
import subprocess
import sys
import threading

import sf_config

BULLETS = "••••"
SERVICE_PREFIX = "shellbot-connect"
MIN_KNOWN = 4                     # a known secret shorter than this is not replaced (it would mask ordinary words)

# Token shapes. Deliberately wide: a false mask costs a word, a missed one costs a key.
_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bya29\.[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\b1//[0-9A-Za-z_\-]{20,}"),
    re.compile(r"\beyJ[0-9A-Za-z_\-]{8,}\.[0-9A-Za-z_\-]{8,}\.[0-9A-Za-z_\-]{4,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=\-]{12,}"),
    re.compile(r"(?i)\b(authorization)\s*[:=]\s*\S+(?:\s+\S+)?"),
]
_LONG_RUN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9_\-]{32,}(?![A-Za-z0-9])")
_ASSIGN = re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)")
_SECRET_KEY_NAME = re.compile(r"(?i)(token|secret|passw(or)?d|credential|api[_-]?key|private[_-]?key|authorization|bearer)")

_known = set()
_known_lock = threading.Lock()


class Unsupported(Exception):
    """This platform has no key store ShellBot can use. `code` is for problems.py."""
    code = "secret_store_unsupported"


class SecretLeak(ValueError):
    """Something that looks like a secret was about to be stored where none may go."""


def remember(value) -> None:
    """Tell mask() about a secret this process handled, so it is replaced wherever it shows up."""
    v = str(value or "")
    if len(v) >= MIN_KNOWN:
        with _known_lock:
            _known.add(v)


def forget_all() -> None:
    with _known_lock:
        _known.clear()


def looks_secret(text) -> bool:
    s = str(text or "")
    return bool(any(p.search(s) for p in _PATTERNS) or _LONG_RUN.search(s))


def mask(text, known=()) -> str:
    """`text` with every secret replaced by ••••: the values handed in or remembered, the shapes tokens
    come in, KEY=VALUE where the name says it is one, and any unbroken run of 32+ letters and digits."""
    s = "" if text is None else str(text)
    with _known_lock:
        values = set(_known)
    values.update(str(k) for k in (known or ()) if k and len(str(k)) >= MIN_KNOWN)
    for v in sorted(values, key=len, reverse=True):
        s = s.replace(v, BULLETS)
    s = _ASSIGN.sub(lambda m: f"{m.group(1)}{m.group(2)}{BULLETS}", s)
    for p in _PATTERNS:
        s = p.sub(BULLETS, s)
    return _LONG_RUN.sub(BULLETS, s)


def assert_clean(obj, path="") -> None:
    """Raise SecretLeak when a structure about to be saved (connectors.json) has a key named like a secret,
    or a value shaped like one. The engine stores neither on purpose; this is the net under it."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if _SECRET_KEY_NAME.search(str(k)) and v not in (None, "", False, True, 0) and not isinstance(v, (dict, list)):
                raise SecretLeak(f"key {path}/{k}")
            assert_clean(v, f"{path}/{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            assert_clean(v, f"{path}[{i}]")
    elif isinstance(obj, str) and looks_secret(obj):
        raise SecretLeak(f"value at {path}")


# ── backends ───────────────────────────────────────────────────────────────
class Backend:
    """get/set/delete by (account, name). Values are str. Never log or return anything but the value."""
    name = "none"

    def get(self, account: str, name: str):
        raise NotImplementedError

    def set(self, account: str, name: str, value: str) -> None:
        raise NotImplementedError

    def delete(self, account: str, name: str) -> bool:
        raise NotImplementedError


class MemoryBackend(Backend):
    """Kept in this process only: what a test uses, and nothing else."""
    name = "memory"

    def __init__(self):
        self.items = {}

    def get(self, account, name):
        return self.items.get((account, name))

    def set(self, account, name, value):
        self.items[(account, name)] = value

    def delete(self, account, name):
        return self.items.pop((account, name), None) is not None


class RefusingBackend(Backend):
    """The default while a test runs: reaching for the real key store is a bug in the test."""
    name = "refusing"

    def _no(self, *a):
        raise Unsupported("a test must pass its own secrets backend (MemoryBackend)")

    get = set = delete = _no


def _service(account: str) -> str:
    return f"{SERVICE_PREFIX}:{account}"


def _quote(v: str) -> str:
    """A value inside the double quotes `security -i` reads: backslash and quote escaped."""
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


class MacKeychain(Backend):
    """macOS Keychain through the `security` program. `security_bin` and `run` are replaceable."""
    name = "keychain"

    def __init__(self, security_bin: str = "/usr/bin/security", run=None):
        self.bin = security_bin
        self._run = run or (lambda argv, stdin=None: subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=15))

    def get(self, account, name):
        r = self._run([self.bin, "find-generic-password", "-s", _service(account), "-a", name, "-w"])
        return r.stdout.rstrip("\n") if r.returncode == 0 else None

    def set(self, account, name, value):
        line = f"add-generic-password -U -s {_quote(_service(account))} -a {_quote(name)} -w {_quote(value)}\n"
        r = self._run([self.bin, "-i"], line)            # the value goes by stdin, not by argument
        if r.returncode != 0:
            raise OSError("keychain write failed")

    def delete(self, account, name):
        r = self._run([self.bin, "delete-generic-password", "-s", _service(account), "-a", name])
        return r.returncode == 0


class WindowsCredMan(Backend):
    """Windows Credential Manager through advapi32 (CredReadW / CredWriteW / CredDeleteW). Not run on
    Windows yet. The target name is the service string plus the secret's name."""
    name = "credman"
    _CRED_TYPE_GENERIC = 1
    _PERSIST_LOCAL_MACHINE = 2

    def _target(self, account, name):
        return f"{_service(account)}:{name}"

    def _api(self):
        import ctypes
        from ctypes import wintypes

        class CRED(ctypes.Structure):
            _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR),
                        ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME),
                        ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_byte)),
                        ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD),
                        ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR),
                        ("UserName", wintypes.LPWSTR)]
        return ctypes, CRED, ctypes.windll.advapi32

    def get(self, account, name):
        ctypes, CRED, adv = self._api()
        p = ctypes.POINTER(CRED)()
        if not adv.CredReadW(self._target(account, name), self._CRED_TYPE_GENERIC, 0, ctypes.byref(p)):
            return None
        try:
            c = p.contents
            return ctypes.string_at(c.CredentialBlob, c.CredentialBlobSize).decode("utf-16-le")
        finally:
            adv.CredFree(p)

    def set(self, account, name, value):
        ctypes, CRED, adv = self._api()
        blob = value.encode("utf-16-le")
        buf = (ctypes.c_byte * len(blob)).from_buffer_copy(blob)
        c = CRED(Type=self._CRED_TYPE_GENERIC, TargetName=self._target(account, name), CredentialBlobSize=len(blob),
                 CredentialBlob=ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)), Persist=self._PERSIST_LOCAL_MACHINE,
                 UserName=name)
        if not adv.CredWriteW(ctypes.byref(c), 0):
            raise OSError("credential write failed")

    def delete(self, account, name):
        ctypes, CRED, adv = self._api()
        return bool(adv.CredDeleteW(self._target(account, name), self._CRED_TYPE_GENERIC, 0))


class UnsupportedBackend(Backend):
    """A platform with no key store ShellBot can use. It says so; it keeps nothing."""
    name = "unsupported"

    def _no(self, *a):
        raise Unsupported("no key store on this platform")

    get = set = delete = _no


def default_backend() -> Backend:
    if sf_config._running_tests():
        return RefusingBackend()
    if sys.platform == "darwin":
        return MacKeychain()
    if sys.platform == "win32":
        return WindowsCredMan()
    return UnsupportedBackend()


def platform_support(backend: Backend = None) -> dict:
    """{"supported": bool, "store": "keychain" | "credman" | …} for the support table the pages show."""
    b = backend or default_backend()
    return {"supported": not isinstance(b, (UnsupportedBackend, RefusingBackend)), "store": b.name}


class SecretStore:
    """The backend, seen by one service: `get/set/delete/has(name)`. Every value that passes is
    remembered by mask() so it can never show in a message of ours."""

    def __init__(self, service: str, backend: Backend = None):
        self.service = service
        self.backend = backend or default_backend()

    def get(self, name: str):
        v = self.backend.get(self.service, name)
        if v:
            remember(v)
        return v

    def set(self, name: str, value: str) -> None:
        if not str(value or "").strip():
            raise ValueError("empty secret")
        remember(value)
        self.backend.set(self.service, name, str(value))

    def delete(self, name: str) -> bool:
        return bool(self.backend.delete(self.service, name))

    def has(self, name: str) -> bool:
        return bool(self.backend.get(self.service, name))
