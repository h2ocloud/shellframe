"""Late-bound handle to the composition root (main.py) for the Api mixins.

The Api mixins (api_*.py) were carved out of main.py's Api class. Their code still
uses helpers and state that live at main.py module level (load_config, IS_WIN,
ACCOUNT_MANAGER, Session, the bridge classes …). They reach them as
``main.<name>`` through the proxy below instead of importing main:

* ShellFrame runs as ``python main.py``, so the module is ``__main__``; an
  ``import main`` inside a mixin would execute main.py a second time.
* The lookup happens at call time, so tests that monkeypatch ``main.X`` and
  ``hot_reload_bridge`` (which rebinds main's bridge globals after a reload)
  keep working exactly as when the methods lived in main.py.

main.py calls ``bind(globals())`` once, before any mixin method can run. The
namespace dict *is* the module's ``__dict__``, so ``main.X = …`` patches are seen,
and it works however main.py was loaded (``__main__``, ``main``, or a test loading
it under another name). This is a transitional seam: a helper that moves into its own module
(e.g. config persistence) should be imported directly instead, and
tests_architecture.py tracks how many ``main.`` references each mixin has.
"""

_ns = None


class _MainModule:
    __slots__ = ()

    def __getattr__(self, name):
        if _ns is None:
            raise RuntimeError("api_host.bind() has not been called (main.py not loaded)")
        try:
            return _ns[name]
        except KeyError:
            raise AttributeError(f"main.py has no global {name!r}") from None

    def __repr__(self):
        return f"<api_host.main -> {(_ns or {}).get('__name__')!r}>"


main = _MainModule()


def bind(namespace):
    """Point ``main`` at main.py's global namespace (``globals()`` or the module)."""
    global _ns
    _ns = namespace if isinstance(namespace, dict) else vars(namespace)
