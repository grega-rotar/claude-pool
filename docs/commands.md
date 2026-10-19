# Commands

Anything that isn't a `--pool-*` command goes to `claude` unchanged.

## Look

| Command | What it does |
|---|---|
| `claude-pool --pool-status` | Every account with its usage, when it resets, and which one runs next |
| `claude-pool --pool-watch [minutes]` | The same table, checked again every 5 minutes (or the minutes given) until Ctrl+C |
| `claude-pool --pool-refresh [name..]` | Check the usage of every account (or the named ones) now |
| `claude-pool --pool-log [N]` | The last N events (default 20): runs, sessions, switches, errors |

## Set up

| Command | What it does |
|---|---|
| `claude-pool --pool-add <name>` | Add an account and paste its token, or pipe it in: `echo "$TOKEN" \| claude-pool --pool-add work` |
| `claude-pool --pool-off <name>` | Take an account out (renames its file to `.off`) |
| `claude-pool --pool-on <name>` | Put it back |
| `claude-pool --pool-check` | Check the setup: claude, folders, permissions, every token |

## Other

| Command | What it does |
|---|---|
| `claude-pool --pool-version` | The version |
| `claude-pool --pool-help` | All commands and settings, and the folders in use right now |
