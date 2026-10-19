# Outbound webhook notifications

> 中文摘要：可選功能。分頁跑完一輪或停下來等人時，ShellFrame 可以 POST 一包 JSON 到你自己
> 設定的 webhook（Slack 轉接、n8n、自架服務都行）。沒設定＝完全關閉：不連網、不印字、不寫 log。
> 設定檔 `~/.config/shellframe/notify.json`（請 `chmod 600`），也可用環境變數覆蓋；
> `sfctl notify --dry-run` 可以檢查設定而不送出。

ShellFrame can tell you when a tab finishes a turn or stops to ask for a decision, by
POSTing a small JSON document to a URL you choose. Nothing is sent unless you configure
a URL. With no URL the feature is fully silent: no network, no output, no log lines.

Two triggers share one payload:

- **`sfctl notify`**: an agent or script reports `DONE`, `BLOCKED`, `QUESTION` or `INFO`.
- **auto-notify** (opt-in): the status monitor sends `DONE` / `BLOCKED` on real transitions.

## Configure

`~/.config/shellframe/notify.json` (ShellFrame never creates or chmods it):

```json
{
  "url": "https://example.com/hook",
  "auth": "Bearer <token>",
  "auto": true,
  "tabs": ["s3"],
  "labels": ["build-*"]
}
```

```bash
chmod 600 ~/.config/shellframe/notify.json   # it holds a token; a looser mode is warned about
```

- `url`: `http` or `https` only. `auth`: the full `Authorization` header value; omit for none.
- `auto`: default `false`. `tabs` / `labels` are optional filters for auto-notify: a tab
  passes if its sid is in `tabs` or its label matches a `labels` glob (case-insensitive).
  Neither set means every tab. `sfctl notify` is not filtered.
- Env vars `SHELLFRAME_NOTIFY_URL` and `SHELLFRAME_NOTIFY_AUTH` override `url` / `auth`
  individually; an empty value counts as unset. They apply to the process that reads them:
  the app's environment for auto-notify, the caller's shell for `sfctl notify`.
- A broken file (bad JSON, wrong field type, non-http URL) is ignored with one warning.
- Auto-notify runs in the app's status monitor, so status detection must be on (the
  default). The file is read when a transition fires, so edits apply without a restart.

## Payload

```json
{"machine": "my-laptop", "sid": "s3", "label": "build-1", "status": "DONE",
 "message": "Turn finished: fix the flaky test", "ts": "2026-01-02T03:04:05+08:00",
 "shellframe_version": "0.40.1"}
```

Headers: `Content-Type: application/json`, `User-Agent: ShellFrame/<version>`, and
`Authorization` only when `auth` is set. `message` is at most 4000 characters.

## What auto-notify sends

- `DONE`: a turn that actually ran (the tab was seen `working`) has ended.
  Message: `Turn finished`, plus the task or summary.
- `BLOCKED`: the tab shows a blocked reason or a permission decision. Message: the reason.
- Detail text is redacted for credentials and cut to 200 characters first.
- Each state is reported once. `done -> idle -> done` and `blocked -> idle -> blocked`
  do not repeat; a new `working` phase re-arms both.
- The first time a tab is seen (including after an app restart) only sets a baseline,
  so tabs already sitting at done or blocked are not announced. A fresh tab that goes from
  its idle prompt to "done" without working, or a startup dialog that is dismissed, sends nothing.
- At most 4 sends are in flight; extra events are dropped with one log line.

## Delivery and secrets

- 5 second overall timeout (DNS included). A 2xx response is success; anything else fails quietly:
  `sfctl notify` prints `WARN notify not delivered: <reason>` on stderr and still exits 0,
  auto-notify writes one line to the debug log. Neither ever blocks the tab.
- Redirects are refused (a 3xx is a failure), so the `Authorization` header cannot be
  forwarded to another host.
- The URL path, query, userinfo and the token are masked everywhere they could be printed
  or logged; messages show `https://host/***` and `*** (N chars)`.

## Check your setup

```bash
sfctl notify --dry-run --status INFO --message "hello"   # masked target + payload, sends nothing
sfctl notify --status INFO --message "hello"             # real send; prints OK or a WARN
```

Without a configured URL, `sfctl notify` prints nothing and exits 0 (`--dry-run` says
`notify: not configured`).
