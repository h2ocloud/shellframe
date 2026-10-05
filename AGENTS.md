# Working on ShellFrame (for coding agents and humans)

ShellFrame is a desktop terminal for AI CLIs: a pywebview page (`web/index.html`)
talks to one Python `Api` object (`main.py` + `api_*.py` mixins); every tab is a
PTY or tmux session.

- **Map first:** [`docs/architecture.md`](docs/architecture.md) says which module owns
  what and where new code goes. [`DEVELOPMENT.md`](DEVELOPMENT.md) has the gotchas.
- **Tests:** `./run_tests.sh` (all `tests_*.py` and `tests_*.js`). It must stay green.
  `tests_architecture.py` enforces the module boundaries and size budgets.
- **Apply a change locally:** `sfctl reload` for `bridge_telegram.py` only;
  `sfctl restart` for `main.py`, `api_*.py`, `web/` or any new module.
- **Release:** `python3 scripts/bump_version.py`, add a `## vX.Y.Z` section to
  `CHANGELOG.md` following [`docs/changelog-guide.md`](docs/changelog-guide.md),
  commit with a message starting `vX.Y.Z`, then `scripts/release.sh`.
- **Public repo:** no personal names, private hosts or credentials in code,
  comments, tests or docs (`tests_repo_hygiene.py` checks this).
- **Do not** import `main` from a mixin, write `config.json` outside
  `save_config`/`update_config`, load-modify-save without `sf_config.CONFIG_LOCK`,
  or hold a lock across `evaluate_js` or a subprocess.
