# ShellFrame Local HTTP API

Optional, opt-in HTTP control surface that lets a **local** agent (e.g. OpenClaw
/ 龍蝦) drive ShellFrame: list/peek tabs, send prompts, delegate, open/close
tabs, and respond to agent signal events.

It is a thin wrapper over the existing `sfctl` command dispatch
(`_execute_sfctl`) — same capabilities the master session and TG bridge use.

## Enable

**Settings → General → Local HTTP API** — flip the toggle (effective immediately,
no restart; the token is auto-generated on first enable and the panel has
"Copy token" / "API docs (Swagger)" buttons).

Or edit `~/.config/shellframe/config.json` by hand:

```json
"api_server": {
  "enabled": true,
  "host": "127.0.0.1",
  "port": 8765,
  "token": "",
  "allowed_ips": ["127.0.0.1", "::1"]
}
```

Then restart ShellFrame (`sfctl restart`). On first enable with a blank `token`,
a random token is generated and written back into the config — read it from
there. The server **fails closed**: enabled with no token = every API call is
rejected (401).

Security, all enforced before any handler runs:
- bound to `host` (default loopback only)
- `allowed_ips` whitelist (exact IPs or CIDR; `"0.0.0.0/0"` = open — don't)
- Bearer token on every endpoint except `/health`, `/openapi.json`, `/docs`

## Docs

- Swagger UI: `http://127.0.0.1:8765/docs`
- OpenAPI 3.0 spec: `http://127.0.0.1:8765/openapi.json`

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/health` | liveness (no token) |
| GET | `/sessions` | list tabs |
| POST | `/sessions` | open tab `{cmd, label}` |
| DELETE | `/sessions/{sid}` | close tab |
| GET | `/sessions/{sid}/peek?lines=200` | read a tab's recent output |
| POST | `/sessions/{sid}/send` | inject a prompt `{text, submit}` |
| POST | `/sessions/{sid}/rename` | rename `{name}` |
| GET | `/status` | roster + live tab states |
| GET | `/roster` | configured worker roles |
| POST | `/delegate` | delegate to a role `{role, task}` |
| GET | `/events?since=<cursor>` | poll agent signal events |

Auth header: `Authorization: Bearer <token>` (or `X-API-Token: <token>`).

## Bidirectional: responding to agent tasks

When a tab raises a signal — `[[SF:RED]]` (needs a decision) or
`[[SF:YELLOW:reason]]` (blocked) — it is pushed to an event queue. A client
polls and replies:

```
GET  /events?since=0
  → {"success": true, "cursor": 7,
     "events": [{"id": 7, "sid": "s5", "label": "coder",
                 "state": "RED", "reason": "...", "ts": ...}]}

# 龍蝦 decides, then answers the agent in that tab:
POST /sessions/s5/send  {"text": "用方案 B，先寫測試", "submit": true}

# next poll uses the returned cursor so each event is seen once:
GET  /events?since=7
```

Events are in-memory (ring buffer, last 500) and reset on restart.

## curl example

```bash
TOKEN=$(python3 -c "import json;print(json.load(open('$HOME/.config/shellframe/config.json'))['api_server']['token'])")
H="Authorization: Bearer $TOKEN"
curl -s -H "$H" localhost:8765/sessions
curl -s -H "$H" "localhost:8765/sessions/s3/peek?lines=80"
curl -s -H "$H" -X POST localhost:8765/sessions/s3/send \
     -d '{"text":"研究這個主題","submit":true}'
curl -s -H "$H" "localhost:8765/events?since=0"
```
