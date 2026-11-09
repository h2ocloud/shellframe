"""Which language ShellFrame writes in when it writes to an agent.

Every brief, playbook, banner and prompt ShellFrame injects into a tab follows the
operating system's preferred language: Traditional Chinese on a Chinese system,
English everywhere else, so the same build works for anyone. The page has its own
language setting (Settings → language); this module is only about the text an
agent reads.

SHELLFRAME_PROMPT_LANG=en|zh overrides the detection (tests pin it with this).

Leaf module: stdlib plus an optional PyObjC import, no `import main`.
"""

import functools
import os
import subprocess
import sys

ENV = "SHELLFRAME_PROMPT_LANG"


def _os_tag() -> str:
    """The OS's first preferred language as a tag ('zh-Hant-TW', 'en-US', …), or ''."""
    if sys.platform == "darwin":
        try:
            from Foundation import NSLocale
            langs = list(NSLocale.preferredLanguages() or [])
            if langs:
                return str(langs[0])
        except Exception:
            pass
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLanguages"],
                                 capture_output=True, text=True, timeout=2).stdout
            first = next((t.strip(' "()\n\t') for t in out.split(",") if t.strip(' "()\n\t')), "")
            if first:
                return first
        except Exception:
            pass
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if os.environ.get(key):
            return os.environ[key]
    try:
        import locale
        return str(locale.getlocale()[0] or "")
    except Exception:
        return ""


@functools.lru_cache(maxsize=1)
def _detected() -> str:
    tag = _os_tag().lower()
    # Windows spells it out ("Chinese (Traditional)_Taiwan"); everyone else uses zh-*.
    return "zh" if tag.startswith(("zh", "chinese")) else "en"


def prompt_lang() -> str:
    """'zh' or 'en'."""
    forced = os.environ.get(ENV, "").strip().lower()
    if forced in ("en", "zh"):
        return forced
    return _detected()


def pick(en: str, zh: str, lang: str = None) -> str:
    return zh if (lang or prompt_lang()) == "zh" else en
