# claude-pool

[![test](https://github.com/grega-rotar/claude-pool/actions/workflows/test.yml/badge.svg)](https://github.com/grega-rotar/claude-pool/actions/workflows/test.yml)

Runs `claude` on whichever of your accounts has usage left. Same arguments, same output: replace `claude` with `claude-pool`.

```sh
claude-pool -p "hi" --output-format json
```

Linux, macOS and Windows. Needs Python 3.8+ and the Claude Code CLI.

## Setup

```sh
# Linux / macOS
curl -fsSL https://github.com/grega-rotar/claude-pool/releases/latest/download/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool

# Windows
py -m pip install git+https://github.com/grega-rotar/claude-pool@v0.6.0
```

For each account, run `claude setup-token`, then:

```sh
claude-pool --pool-add work    # paste the token
claude-pool --pool-check       # tests every token, shows usage
```

Tokens are saved in `~/.config/claude-pool/<name>.token` (Windows: `%APPDATA%\claude-pool\`). API keys work too.

## Use

Run `claude-pool` wherever you'd run `claude`:

```php
Process::run(['claude-pool', '-p', $prompt, '--output-format', 'json']);
```

JSON output gets a `pool_account` field. Exit code `75` means every account is full.

Commands: `--pool-status`, `--pool-refresh`, `--pool-check`, `--pool-add <name>`, `--pool-off <name>`, `--pool-on <name>`, `--pool-log [N]`, `--pool-help`. Everything else goes to `claude`.

## How it picks

- Uses up the account whose week resets soonest first, since leftover usage is lost at the reset.
- Moves on at 90% of the 5-hour limit.
- Usage older than 5 minutes is checked with a tiny Haiku call (~$0.0001), so other apps' use counts.
- A run cut off by a limit is rerun on the next account, but only if it hadn't used any tools yet.

## Settings

All optional. Set them as environment variables or in a `.env`.

| Variable | Default | |
|---|---|---|
| `CLAUDE_POOL_TOKEN_<NAME>` | | Account token without a file (`CLAUDE_POOL_KEY_<NAME>` for an API key) |
| `CLAUDE_POOL_DIR` | `~/.config/claude-pool` | Account folder |
| `CLAUDE_POOL_STATE` | `~/.local/state/claude-pool/usage.json` | Usage file |
| `CLAUDE_POOL_ENV_FILE` | nearest `.env` going up | `.env` to read |
| `CLAUDE_POOL_BINARY` | `claude` | The real claude |
| `CLAUDE_POOL_ACCOUNT` | | Always use this account |
| `CLAUDE_POOL_STRATEGY` | `drain` | `balance` = least used first |
| `CLAUDE_POOL_SWITCH_AT` | `90` | 5-hour % to move on |
| `CLAUDE_POOL_SPREAD_AT` | `75` | 5-hour % after which a busy account gets no second run |
| `CLAUDE_POOL_FRESH` | `300` | Seconds between usage checks, `0` = off |
| `CLAUDE_POOL_LIMIT` | `100` | % that counts as full |
| `CLAUDE_POOL_RERUN` | `safe` | `safe`, `always` or `never` |
| `CLAUDE_POOL_ALERT` | | Command run when accounts are full or a token breaks (max once an hour, gets `$CLAUDE_POOL_MESSAGE`) |
| `CLAUDE_POOL_LOG` | next to the usage file | Event log |

### .env

claude-pool reads the first `.env` it finds, starting in the current folder and going up. Only `CLAUDE_POOL_*` lines are used, so the app's other secrets never reach claude.

```sh
CLAUDE_POOL_TOKEN_WORK=sk-ant-oat01-...
CLAUDE_POOL_TOKEN_WORK_2=sk-ant-oat01-...   # account "work-2"
```

Real environment variables win over the `.env`. A `.token` file wins over a variable with the same name.

## One pool for several users

To let `www-data`, `deploy` and you share accounts and usage:

```sh
sudo groupadd claude-pool && sudo usermod -aG claude-pool www-data
sudo install -d -m 2770 -g claude-pool /etc/claude-pool /var/lib/claude-pool
sudo env CLAUDE_POOL_DIR=/etc/claude-pool claude-pool --pool-add work
```

Then give every user `CLAUDE_POOL_DIR=/etc/claude-pool` and `CLAUDE_POOL_STATE=/var/lib/claude-pool/usage.json`. Service users also need a writable home for claude (`CLAUDE_CONFIG_DIR`) and claude on their `PATH` (or `CLAUDE_POOL_BINARY`).

Several servers can use the same tokens; each sees the others' use at the 5-minute check.

## Tests

```sh
python3 -m unittest discover tests
sudo sh tests/multiuser.sh    # Linux, two real users
```

Using several accounts to get around usage limits breaks Anthropic's terms. MIT.
