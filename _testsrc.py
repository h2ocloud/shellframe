"""Shared helpers for tests that read ShellFrame source text.

The Api class is split across main.py and the api_*.py mixins (God-class
decomposition). A test that looks for a method body or a sfctl branch by text
should search all of them, not main.py alone, so it keeps passing when a method
moves between files without changing.
"""
import inspect
import pathlib
import re

HERE = pathlib.Path(__file__).resolve().parent


_HOST_PREFIX = re.compile(r"(?<![\w.])main\.(?!py\b)(?=[A-Za-z_])")


def fold_host(src: str) -> str:
    """Drop the late-bound ``main.`` prefix the mixins use for main.py globals.

    A mixin writes ``main.Session(...)`` / ``main.IS_WIN`` where main.py wrote
    ``Session(...)`` / ``IS_WIN``; folding it back lets a text check written
    against the original code keep matching. ``main.py`` in strings is left alone.
    """
    return _HOST_PREFIX.sub("", src)


def mixin_files():
    """api_*.py files that define an Api mixin, in a stable order."""
    return [p for p in sorted(HERE.glob("api_*.py"))
            if "ApiMixin:" in p.read_text(encoding="utf-8")]


def app_source() -> str:
    """main.py followed by every Api mixin module."""
    parts = [(HERE / "main.py").read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in mixin_files()]
    return fold_host("\n".join(parts))


def api_class_source(api_cls) -> str:
    """Source of the Api class and every class it inherits methods from."""
    return fold_host("\n".join(inspect.getsource(c) for c in api_cls.__mro__ if c is not object))
