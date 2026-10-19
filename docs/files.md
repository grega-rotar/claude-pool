# Files

Every command only reads or writes these files, so you can do the same with a text editor.

| What | Where (default) | Change it with |
|---|---|---|
| Accounts | `~/.config/claude-pool/` (Windows: `%APPDATA%\claude-pool\`) | `CLAUDE_POOL_DIR` |
| One account | `<name>.token` or `<name>.key`, the secret alone on one line | |
| An account taken out | `<name>.token.off` / `<name>.key.off` | |
| Usage of every account | `~/.local/state/claude-pool/usage.json` | `CLAUDE_POOL_STATE` |
| Event log | `events.log` next to the usage file | `CLAUDE_POOL_LOG` |
| Settings | environment variables, or `CLAUDE_POOL_*` lines in the nearest `.env` | `CLAUDE_POOL_ENV_FILE` |

`claude-pool --pool-help` prints the folders it uses right now. The account folder is read again on every run, so a change counts at once.

## Start the usage numbers over

Delete `usage.json`. The next run or `claude-pool --pool-refresh` checks every account again.

## Update claude-pool

Run the install line from the README again. It replaces the one file. Accounts, usage and settings stay. With pip, add `--upgrade`; with pipx, run `pipx upgrade claude-pool`.

## Uninstall

Delete `~/.local/bin/claude-pool` (or `pip uninstall claude-pool`, `pipx uninstall claude-pool`). To remove your accounts and usage too, delete the folders in the table above.
