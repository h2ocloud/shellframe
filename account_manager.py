"""Per-provider account profiles for ShellFrame.

The ShellFrame config contains only non-sensitive account metadata and refs.
Provider credential snapshots live in mode-700/600 profile directories so a
session can receive its own CODEX_HOME or CLAUDE_CONFIG_DIR without changing
the credentials used by any other running tab.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path


PROVIDERS = ("codex", "claude")


def _empty_accounts():
    return {"global": {p: None for p in PROVIDERS},
            "profiles": {p: [] for p in PROVIDERS},
            "sessions": {}}


def _jwt_claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        part += "=" * (-len(part) % 4)
        return json.loads(base64.urlsafe_b64decode(part.encode()))
    except Exception:
        return {}


def _safe_ref(provider: str, identity: str) -> str:
    digest = hashlib.sha256(f"{provider}:{identity}".encode()).hexdigest()[:16]
    return f"{provider}-{digest}"


def _chmod_private(path: Path, mode: int):
    try:
        path.chmod(mode)
    except OSError:
        pass


class AccountManager:
    def __init__(self, root=None, home=None, keychain_getter=None):
        self.home = Path(home or Path.home()).expanduser()
        self.root = Path(root or (self.home / ".config" / "shellframe" / "account-profiles"))
        self.keychain_getter = keychain_getter or self._read_keychain

    def _profile_dir(self, provider: str, ref: str) -> Path:
        return self.root / provider / ref

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            with path.open(encoding="utf-8") as fh:
                value = json.load(fh)
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def _read_keychain(self) -> dict:
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                value = json.loads(result.stdout)
                return value if isinstance(value, dict) else {}
        except Exception:
            pass
        return {}

    def _discover_codex(self):
        path = self.home / ".codex" / "auth.json"
        raw = self._read_json(path)
        tokens = raw.get("tokens") or {}
        claims = _jwt_claims(tokens.get("access_token", ""))
        profile = claims.get("https://api.openai.com/profile") or {}
        auth = claims.get("https://api.openai.com/auth") or {}
        account_id = tokens.get("account_id") or ""
        email = profile.get("email") or ""
        if not raw or not (account_id or email or tokens.get("access_token")):
            return None
        identity = account_id or email or tokens.get("access_token", "")[:24]
        return {
            "id": _safe_ref("codex", identity),
            "email": email,
            "plan": auth.get("chatgpt_plan_type") or "",
            "label": email or "Codex 帳號",
            "source": str(path),
            "credential": raw,
        }

    def _discover_claude(self):
        config = self._read_json(self.home / ".claude.json")
        account = config.get("oauthAccount") or {}
        credential = self.keychain_getter() or {}
        oauth = credential.get("claudeAiOauth") or {}
        # Linux/Windows profiles use this file; accepting it also makes the
        # manager testable without macOS Keychain.
        if not oauth:
            credential = self._read_json(self.home / ".claude" / ".credentials.json")
            oauth = credential.get("claudeAiOauth") or {}
        email = account.get("emailAddress") or oauth.get("emailAddress") or ""
        org = account.get("organizationName") or oauth.get("organizationName") or ""
        plan = oauth.get("subscriptionType") or ""
        access = oauth.get("accessToken") or ""
        if not (email or org or access):
            return None
        identity = email or org or access[:24]
        return {
            "id": _safe_ref("claude", identity),
            "email": email,
            "organization": org,
            "plan": plan,
            "label": email or org or "Claude 帳號",
            "source": "Claude Code-credentials",
            "credential": credential,
        }

    def discover(self, provider: str):
        if provider == "codex":
            return self._discover_codex()
        if provider == "claude":
            return self._discover_claude()
        raise ValueError(f"unknown provider: {provider}")

    def write_profile(self, provider: str, ref: str, credential: dict):
        directory = self._profile_dir(provider, ref)
        directory.mkdir(parents=True, exist_ok=True)
        _chmod_private(self.root, 0o700)
        _chmod_private(self.root / provider, 0o700)
        _chmod_private(directory, 0o700)
        if provider == "codex":
            target = directory / "auth.json"
        else:
            target = directory / ".credentials.json"
        with target.open("w", encoding="utf-8") as fh:
            json.dump(credential, fh, ensure_ascii=False)
            fh.write("\n")
        _chmod_private(target, 0o600)
        return directory

    def _metadata(self, discovered: dict):
        return {k: discovered.get(k, "") for k in
                ("id", "email", "label", "plan", "organization")}

    def _add_profile(self, accounts: dict, provider: str, discovered: dict):
        profiles = accounts["profiles"].setdefault(provider, [])
        ref = discovered["id"]
        item = self._metadata(discovered)
        replaced = False
        for idx, current in enumerate(profiles):
            if current.get("id") == ref:
                profiles[idx] = {**current, **item, "updated_at": int(time.time())}
                replaced = True
                break
        if not replaced:
            item["updated_at"] = int(time.time())
            profiles.append(item)
        self.write_profile(provider, ref, discovered.get("credential") or {})
        return ref

    def ensure(self, cfg: dict) -> bool:
        accounts = cfg.setdefault("accounts", _empty_accounts())
        changed = False
        for key, default in _empty_accounts().items():
            if not isinstance(accounts.get(key), dict):
                accounts[key] = default
                changed = True
        for provider in PROVIDERS:
            if not isinstance(accounts["profiles"].get(provider), list):
                accounts["profiles"][provider] = []
                changed = True
            discovered = self.discover(provider)
            if discovered:
                ref = self._add_profile(accounts, provider, discovered)
                if accounts["global"].get(provider) is None:
                    accounts["global"][provider] = ref
                    changed = True
        return changed

    def sync_current(self, cfg: dict, provider: str):
        accounts = cfg.setdefault("accounts", _empty_accounts())
        discovered = self.discover(provider)
        if not discovered:
            return None
        ref = self._add_profile(accounts, provider, discovered)
        if accounts["global"].get(provider) is None:
            accounts["global"][provider] = ref
        return ref

    def profile(self, cfg: dict, provider: str, ref: str):
        for item in ((cfg.get("accounts", {}).get("profiles", {}) or {}).get(provider, []) or []):
            if item.get("id") == ref:
                return item
        return None

    def valid_ref(self, cfg: dict, provider: str, ref: str) -> bool:
        return bool(ref and self.profile(cfg, provider, ref)
                    and self._profile_dir(provider, ref).is_dir())

    def set_global(self, cfg: dict, provider: str, ref: str):
        if provider not in PROVIDERS:
            raise ValueError("unknown provider")
        if not self.valid_ref(cfg, provider, ref):
            raise ValueError("account is not logged in")
        cfg.setdefault("accounts", _empty_accounts())["global"][provider] = ref

    def session_refs(self, cfg: dict, sid: str | None = None):
        accounts = cfg.get("accounts") or _empty_accounts()
        if sid:
            saved = (accounts.get("sessions") or {}).get(str(sid)) or {}
            return {provider: saved.get(provider, (accounts.get("global") or {}).get(provider))
                    for provider in PROVIDERS}
        return {provider: (accounts.get("global") or {}).get(provider)
                for provider in PROVIDERS}

    def set_session_ref(self, cfg: dict, sid: str, provider: str, ref: str):
        if provider not in PROVIDERS:
            raise ValueError("unknown provider")
        if not self.valid_ref(cfg, provider, ref):
            raise ValueError("account is not logged in")
        sessions = cfg.setdefault("accounts", _empty_accounts()).setdefault("sessions", {})
        sessions.setdefault(str(sid), {})[provider] = ref

    def safe_state(self, cfg: dict, sid: str | None = None):
        accounts = cfg.get("accounts") or _empty_accounts()
        current_refs = self.session_refs(cfg, sid)
        global_refs = self.session_refs(cfg)
        result = {"session_id": sid or "", "providers": {}}
        for provider in PROVIDERS:
            profiles = (accounts.get("profiles") or {}).get(provider, []) or []
            result["providers"][provider] = {
                "current": self.profile(cfg, provider, current_refs.get(provider)),
                "global": self.profile(cfg, provider, global_refs.get(provider)),
                "accounts": [dict(item) for item in profiles],
                "logged_in": bool(profiles),
            }
        return result

    def env_for(self, provider: str, ref: str) -> dict:
        directory = self._profile_dir(provider, ref)
        if not directory.is_dir():
            return {}
        if provider == "codex":
            return {"CODEX_HOME": str(directory)}
        env = {"CLAUDE_CONFIG_DIR": str(directory)}
        credential = self._read_json(directory / ".credentials.json")
        token = (credential.get("claudeAiOauth") or {}).get("accessToken")
        if token:
            # Claude Code documents this process-scoped token override. It
            # prevents one tab's account from changing another tab's Keychain.
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        return env
