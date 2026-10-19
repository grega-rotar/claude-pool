# Where things live, and doing it by hand

Every command in the README only reads or writes the files below. You can do the same with a text editor or the shell.

## Files

| What | Where (default) | Change it with |
|---|---|---|
| Accounts | `~/.config/claude-pool/` (Windows: `%APPDATA%\claude-pool\`) | `CLAUDE_POOL_DIR` |
| One account | `<name>.token` (a `claude setup-token` token) or `<name>.key` (an API key), the secret alone on one line | |
| An account taken out | `<name>.token.off` / `<name>.key.off` | |
| Usage of every account | `~/.local/state/claude-pool/usage.json` | `CLAUDE_POOL_STATE` |
| Event log | `events.log` next to the usage file | `CLAUDE_POOL_LOG` |
| Settings | environment variables, or `CLAUDE_POOL_*` lines in the nearest `.env` | `CLAUDE_POOL_ENV_FILE` |

`claude-pool --pool-help` prints the folders it uses right now. The folder is read again on every run, so a change counts at once.

## Add an account

```sh
claude setup-token                      # log in to the account in the browser it opens, copy the token
printf '%s\n' 'sk-ant-oat01-...' > ~/.config/claude-pool/work.token
chmod 600 ~/.config/claude-pool/work.token
claude-pool --pool-check                # "work: token works"
```

The file name is the account's name. An API key goes in `work.key` instead. Without a file: `CLAUDE_POOL_TOKEN_WORK=sk-ant-oat01-...` in the environment or a `.env` (`CLAUDE_POOL_KEY_WORK` for a key).

## Renew a token

`claude setup-token` tokens last a year. Make a new one the same way and write it over the old file. The account keeps its name and usage.

## Take an account out, put it back, remove it

```sh
mv ~/.config/claude-pool/work.token ~/.config/claude-pool/work.token.off    # out (same as --pool-off work)
mv ~/.config/claude-pool/work.token.off ~/.config/claude-pool/work.token    # back (same as --pool-on work)
rm ~/.config/claude-pool/work.token.off                                     # gone for good
```

## Change a setting

Every setting is in [settings.md](settings.md). Set it where the caller runs:

- a shell: `export CLAUDE_POOL_SWITCH_AT=80`, or add it to `~/.bashrc`
- one project: a `CLAUDE_POOL_SWITCH_AT=80` line in its `.env`
- a systemd service: `Environment=CLAUDE_POOL_SWITCH_AT=80`, or a line in its `EnvironmentFile`

## Start the usage numbers over

Delete `usage.json`. The next run or `claude-pool --pool-refresh` checks every account again.

## Update claude-pool

Run the install line from the README again. It replaces the one file. Accounts, usage and settings stay.
