# claude-pool

Runs `claude` on whichever of your accounts has usage left. Use it exactly like `claude`.

## Install

```sh
curl -fsSL https://github.com/grega-rotar/claude-pool/releases/latest/download/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool
```

## Add accounts

Run `claude setup-token` for each account, then:

```sh
claude-pool --pool-add work
```

Or put them in a `.env`:

```sh
CLAUDE_POOL_TOKEN_WORK=sk-ant-oat01-...
```

## Use

```sh
claude-pool -p "hi"
```

## Commands

| Command | What it does |
|---|---|
| `claude-pool --pool-status` | Every account with its usage, when it resets, and which one runs next |
| `claude-pool --pool-watch [minutes]` | The same table, checked again every 5 minutes (or the minutes given) until Ctrl+C |
| `claude-pool --pool-refresh [name..]` | Check the usage of every account (or the named ones) now |
| `claude-pool --pool-check` | Check the setup: claude, folders, permissions, every token |
| `claude-pool --pool-add <name>` | Add an account and paste its token, or pipe it in: `echo "$TOKEN" \| claude-pool --pool-add work` |
| `claude-pool --pool-off <name>` | Take an account out (renames its file to `.off`) |
| `claude-pool --pool-on <name>` | Put it back |
| `claude-pool --pool-log [N]` | The last N events (default 20): runs, switches, errors |
| `claude-pool --pool-version` | The version |
| `claude-pool --pool-help` | All commands and settings |

Anything else goes to `claude` unchanged.

More: [settings](docs/settings.md), [how it works](docs/how-it-works.md), [servers](docs/server.md).

Using several accounts to get around usage limits breaks Anthropic's terms. MIT.
