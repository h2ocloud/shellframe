# The guide: a tutorial bound to the features

A newly installed ShellBot walks its person through the features once (the first-run tour),
and the sidebar's **Guide** button lists every feature with where it is, what it does, and
what is new since the person last looked. The words live in one file, `guide.json`, and a
test fails when a feature and its entry drift apart, so the guide is kept up as features are
added rather than written once and left.

## Where things are

| Piece | Does |
|---|---|
| `guide.json` | One entry per feature. The single source for the desktop now and the phone app later |
| `guide.py` | Reads it; who gets the tour; what is new since a version. Stdlib only |
| `api_guide.py` | js_api `guide_data` and `guide_mark` |
| `web/guide.js` + `guide.css` | Draws the tour and the help list; finds each feature by `data-guide="<id>"` |
| `tests_guide.py` | The binding (below) |
| `tests_guide_ui.py` | The page in Chromium and WebKit: first launch, an existing install, "show me", English, a small window. Screenshots in `qa-shots/guide-*.png` |

## An entry

```json
{
  "id": "telegram",
  "surfaces": ["desktop"],
  "since": "0.44.6",
  "flag": "experimental_x",
  "open": "settings:bridge",
  "title": {"en": "Telegram", "zh": "Telegram"},
  "body": {"en": "…", "zh": "…"}
}
```

- `id`: where it is on screen. The desktop marks that element `data-guide="<id>"` (in
  `index.html`, or `setAttribute('data-guide', '<id>')` / a template string in a `web/*.js`).
  More than one element may carry it; the first visible one is used.
- `surfaces`: `desktop`, and `ios` once the app marks its view with the same id.
- `since`: the version from which it counts as new. Entries written when the guide began carry
  0.44.6; a later feature carries the version it ships in.
- `flag` (optional): the `settings` switch it needs. With the switch off the entry still shows,
  marked off, and "show me" points at the switch.
- `open` (optional): `settings:<tab>`, the Settings tab that shows it; the tour and "show me" open it.
- Title at most 40 characters, body at most 260, in both languages. Say what it does and where,
  in the person's words; no internal names.

`tours.first_run` lists the tour's steps in order. A step not on the page at that moment (no tab
yet, a button that appears later) is left out.

## What the test holds you to

- Every desktop entry has its `data-guide` mark on the page, and every mark has an entry.
- Every `experimental_*` switch in `DEFAULT_CONFIG` is some entry's `flag`, or `no_guide` says why not.
- The newest CHANGELOG version with an `### Added` section has an entry with that `since`, or
  `added_without_guide` gives the reason (an `sfctl` verb, say, with nothing on screen).
- `reviewed_for` covers the current minor version: once per minor release, read the guide
  through and raise it.

## Who sees what

- A first launch (no `config.json`): `config["guide"]["tour"]` is `"pending"`, the tour starts by
  itself once the page is up, and ends `"done"` (Done or Skip).
- A computer that already ran ShellBot: `"skip"`, and everything up to the running version counts
  as seen. No popup after an update; the Guide button counts what is new.
- The state is `config["guide"]`, not `settings`: the page saves `settings` whole from its own copy
  (`save_settings`), which would put `"pending"` back after the tour.
