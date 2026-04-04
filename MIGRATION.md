# Migration Guide: --channels → ShellFrame Bridge

If you're currently running Claude Code with `--channels plugin:telegram`, this guide helps you migrate to ShellFrame's built-in Telegram Bridge without losing your existing bot setup.

## What changes

| | `--channels` (before) | ShellFrame Bridge (after) |
|---|---|---|
| Bot management | Claude Code plugin | ShellFrame built-in |
| Config location | `~/.claude/channels/telegram/` | `~/.config/shellframe/config.json` |
| Bot token | `~/.claude/channels/telegram/.env` | ShellFrame Settings → Bridge |
| Access control | `~/.claude/channels/telegram/access.json` | ShellFrame Bridge whitelist |
| Process | Separate bun server.ts | Integrated in ShellFrame |
| Multi-bot | One per claude instance | Multiple tabs, one bot each |
| Pause/resume | Not available | One-click toggle |

## Step 1: Read your existing config

Your current Telegram channel config is stored in:

```
~/.claude/channels/telegram/.env          # BOT_TOKEN=xxx
~/.claude/channels/telegram/access.json   # {"allowlist": [...], "policy": ...}
```

### For AI assistants (Claude/Codex)

Read these files to extract the values:

```bash
# Bot token
cat ~/.claude/channels/telegram/.env | grep BOT_TOKEN

# Allowed user IDs
cat ~/.claude/channels/telegram/access.json
```

The `access.json` format:
```json
{
  "allowlist": [{"user_id": "8535404559", "username": "..."}],
  "policy": {"dm": "allowlist", "group": "deny"}
}
```

## Step 2: Configure ShellFrame Bridge

### Option A: Via GUI

1. Open ShellFrame (`shellframe`)
2. Open a new session (e.g., `claude`)
3. Click the **TG** button in the tab bar
4. Paste your bot token
5. Enter allowed user IDs (comma-separated)
6. Click **Connect**

### Option B: Via config file

Write directly to `~/.config/shellframe/config.json`:

```json
{
  "presets": [
    {
      "name": "Claude + TG",
      "cmd": "claude",
      "icon": "📱"
    }
  ],
  "bridges": {
    "telegram": [
      {
        "preset": "Claude + TG",
        "bot_token": "YOUR_BOT_TOKEN_HERE",
        "allowed_users": [8535404559],
        "prefix_enabled": true,
        "initial_prompt": "You are now receiving messages bridged from Telegram. Keep responses concise and mobile-friendly. Messages from Telegram will be prefixed with [TG username]."
      }
    ]
  },
  "settings": {
    "fontSize": 14,
    "language": "zh-TW"
  }
}
```

### Option C: Auto-migration script

Run this in your terminal to auto-migrate:

```bash
# Extract existing config and write to ShellFrame
python3 -c "
import json, os

# Read existing channel config
env_path = os.path.expanduser('~/.claude/channels/telegram/.env')
access_path = os.path.expanduser('~/.claude/channels/telegram/access.json')
sf_config_path = os.path.expanduser('~/.config/shellframe/config.json')

# Get bot token
bot_token = ''
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            if line.startswith('BOT_TOKEN='):
                bot_token = line.strip().split('=', 1)[1]

# Get allowed users
allowed_users = []
if os.path.exists(access_path):
    with open(access_path) as f:
        access = json.load(f)
        for entry in access.get('allowlist', []):
            uid = entry.get('user_id', '')
            if uid:
                allowed_users.append(int(uid))

# Read existing ShellFrame config
if os.path.exists(sf_config_path):
    with open(sf_config_path) as f:
        config = json.load(f)
else:
    config = {'presets': [], 'settings': {}}

# Add bridge config
config['bridges'] = config.get('bridges', {})
config['bridges']['telegram'] = [{
    'preset': 'Claude + TG',
    'bot_token': bot_token,
    'allowed_users': allowed_users,
    'prefix_enabled': True,
    'initial_prompt': 'You are now receiving messages bridged from Telegram. Keep responses concise and mobile-friendly. Messages from Telegram will be prefixed with [TG username].',
}]

# Write back
with open(sf_config_path, 'w') as f:
    json.dump(config, f, indent=2, ensure_ascii=False)

print(f'Migrated successfully!')
print(f'  Bot token: {bot_token[:10]}...')
print(f'  Allowed users: {allowed_users}')
print(f'  Config: {sf_config_path}')
"
```

## Step 3: Stop the old channel

```bash
# If running as claude --channels, just close that terminal/process

# If running as LaunchAgent (already disabled for most users):
# launchctl remove ai.openclaw.gateway  # or whatever your agent is named
```

## Step 4: Verify

1. Open ShellFrame
2. Create a new `claude` session
3. Click **TG** → your bot should auto-connect (if config was written)
4. Send a test message from Telegram
5. Verify the message appears in the ShellFrame terminal

## Differences to be aware of

1. **No `--channels` flag needed** — ShellFrame handles the bridge natively
2. **Pause/resume** — Click the TG button to pause when you're at the computer
3. **Multiple bots** — Open multiple tabs, each with a different bot token
4. **System prompt** — ShellFrame sends an initial prompt to optimize CLI output for TG
5. **Output buffering** — Responses are sent to TG after 2 seconds of idle (avoids message spam)

## Rollback

If you want to go back to `--channels`:

```bash
# Just run claude with the channel flag as before
claude --channels plugin:telegram@claude-plugins-official
```

Your old config in `~/.claude/channels/telegram/` is untouched — ShellFrame never modifies it.
