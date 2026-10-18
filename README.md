# claude-pool

Runs `claude` on whichever of your accounts has usage left. It takes the same arguments as `claude` and prints the same output.

```sh
claude-pool -p "hi" --output-format json
```

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/grega-rotar/claude-pool/main/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool
```

## Accounts

Put one file per account in `~/.config/claude-pool/`:

- `<name>.token`: a token from `claude setup-token`
- `<name>.key`: an API key

Rename a file to `.off` to disable it. With no files, `claude` runs with your normal login.

```sh
claude-pool --pool-status    # usage per account
claude-pool --pool-refresh   # check every account now
```

## How it knows usage

Every Claude run reports the account's real usage, and claude-pool saves it. If an account's numbers are older than 5 minutes (other apps may have used it), it checks that account first with a tiny Haiku call. An account whose token fails is skipped.

## How it picks

It uses up the account whose week ends soonest, because leftover usage is lost at the reset. When that account's 5-hour window is at 90%, it moves to the next one. Fresh accounts are saved for last.

If a run hits a limit, it starts over on the next account. If every account is full, it exits with code 75.

## Cost

- **Time:** about 60 ms per call, plus 2-3 s when it checks accounts (all at once, at most every 5 minutes).
- **Usage:** each check is one tiny Haiku call (about $0.0001 per account).
- **Reruns:** a run cut off by a limit is wasted, since it starts again from scratch.

## Requirements

- **OS:** Linux or macOS. Windows doesn't work: it uses `fcntl` and `select`.
- **Python:** 3.8 or newer, standard library only.
- **Claude Code CLI:** installed and on `PATH`, or set `CLAUDE_POOL_BINARY` to its full path.
- **Network:** outbound HTTPS to `api.anthropic.com`.
- **Tokens:** run `claude setup-token` once per account. It works on any machine, so copy the token to the server.
- **One user:** every app that calls `claude-pool` must read the same account folder and write the same state file. If they run as different users (for example `www-data` for a web app and your own user for scripts), set `CLAUDE_POOL_DIR` and `CLAUDE_POOL_STATE` to paths both can use, through a shared group:

  ```sh
  sudo install -d -m 2770 -g claude /etc/claude-pool /var/lib/claude-pool
  # each token file: chmod 640, group claude
  export CLAUDE_POOL_DIR=/etc/claude-pool CLAUDE_POOL_STATE=/var/lib/claude-pool/usage.json
  ```

- **Several servers:** each one keeps its own state file. Using the same accounts on several servers is fine: the 5-minute check reads the real usage, so each server sees what the others used.
- **Services:** systemd and PHP-FPM don't load your shell profile, so set the variables in the unit file or the app's `.env`.

## Settings

All settings are environment variables and all are optional.

| Variable | Default | What it does |
|---|---|---|
| `CLAUDE_POOL_DIR` | `~/.config/claude-pool` | Folder with the account files |
| `CLAUDE_POOL_STATE` | `~/.local/state/claude-pool/usage.json` | Saved usage, shared by all callers |
| `CLAUDE_POOL_BINARY` | `claude` | The real Claude CLI |
| `CLAUDE_POOL_ACCOUNT` | none | Always use this account, with no switching and no checks |
| `CLAUDE_POOL_STRATEGY` | `drain` | `drain`: soonest weekly reset first. `balance`: lowest usage first |
| `CLAUDE_POOL_SWITCH_AT` | `90` | Moves to the next account once the 5-hour window passes this percent |
| `CLAUDE_POOL_FRESH` | `300` | Seconds before an account's numbers are checked again. `0` turns checks off |
| `CLAUDE_POOL_LIMIT` | `100` | Percent at which an account counts as full |

## Notes

- Using several accounts to get around usage limits breaks Anthropic's terms.

MIT
