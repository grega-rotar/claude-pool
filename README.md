# claude-pool

Runs `claude` on whichever of your accounts has usage left. It takes the same arguments as `claude` and prints the same output, so you only replace `claude` with `claude-pool`.

```sh
claude-pool -p "hi" --output-format json
```

Works on Linux, macOS and Windows. You need Python 3.8+ and the Claude Code CLI.

## Setup

**1. Install**

Linux / macOS:

```sh
curl -fsSL https://raw.githubusercontent.com/grega-rotar/claude-pool/main/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool
```

Windows (PowerShell). This installs a `claude-pool` command:

```powershell
py -m pip install git+https://github.com/grega-rotar/claude-pool
```

**2. Add accounts**

For each account, run `claude setup-token`, log in as that account, and save the token it prints into a file:

| OS | Folder |
|---|---|
| Linux / macOS | `~/.config/claude-pool/` |
| Windows | `%APPDATA%\claude-pool\` |

```sh
mkdir -p ~/.config/claude-pool
echo "sk-ant-oat01-..." > ~/.config/claude-pool/work.token
chmod 600 ~/.config/claude-pool/*.token
```

The file name is the account name: `work.token`, `personal.token` and so on. For an API key, use `<name>.key`. To turn an account off, rename the file to `.off`.

**3. Check**

```sh
claude-pool --pool-refresh
```

This shows every account with its usage. The one marked `<- next` is used next.

## Use it in other apps

Anywhere you run `claude`, run `claude-pool` instead.

```sh
# shell / cron
claude-pool -p "summarize today's errors" < errors.log
```

```python
# Python
subprocess.run(["claude-pool", "-p", prompt, "--output-format", "json"], capture_output=True, text=True)
```

```js
// Node
execFileSync("claude-pool", ["-p", prompt, "--output-format", "json"], { encoding: "utf8" })
```

```php
// PHP / Laravel
Process::run(['claude-pool', '-p', $prompt, '--output-format', 'json']);
```

If an app lets you set the path to `claude`, point it at `claude-pool`.

JSON output gets one extra field, `pool_account`, which says which account ran it. If every account is full, it exits with code `75`, so your app can retry later.

## How it works

- **Usage:** every run reports the account's real usage, and claude-pool saves it. Numbers older than 5 minutes are checked first with a tiny Haiku call (about $0.0001), so use by other apps shows up too.
- **Picking:** it uses up the account whose week ends soonest, because leftover usage is lost at the reset. At 90% of the 5-hour limit, it moves to the next account. New accounts go last.
- **Limits:** if a run hits a limit, it starts over on the next account. Tool calls in the cut-off run happen a second time.
- **Cost:** about 60 ms per call, plus 2-3 s when accounts are checked.

## Settings

You don't need to change anything. If you want to, set these as environment variables.

| Variable | Default | What it does |
|---|---|---|
| `CLAUDE_POOL_DIR` | see step 2 | Folder with the account files |
| `CLAUDE_POOL_STATE` | `~/.local/state/claude-pool/usage.json`, Windows `%LOCALAPPDATA%\claude-pool\usage.json` | Saved usage |
| `CLAUDE_POOL_BINARY` | `claude` | Path to the real Claude CLI |
| `CLAUDE_POOL_ACCOUNT` | none | Always use this one account |
| `CLAUDE_POOL_STRATEGY` | `drain` | `balance` uses the least-used account instead |
| `CLAUDE_POOL_SWITCH_AT` | `90` | 5-hour % at which it moves to the next account |
| `CLAUDE_POOL_FRESH` | `300` | Seconds between usage checks. `0` turns them off |
| `CLAUDE_POOL_LIMIT` | `100` | % at which an account counts as full |

### Where to set them

Set them where the app that calls `claude-pool` runs. Services don't read your shell profile.

| Where | How |
|---|---|
| Your terminal (Linux / macOS) | Add `export CLAUDE_POOL_FRESH=600` to `~/.bashrc` or `~/.zshrc` |
| Windows | `setx CLAUDE_POOL_FRESH 600`, then open a new terminal. Or: Settings → System → About → Advanced system settings → Environment Variables |
| systemd service | `Environment=CLAUDE_POOL_FRESH=600` under `[Service]`, then `systemctl daemon-reload` and restart the service |
| cron | Put `CLAUDE_POOL_FRESH=600` on a line above the jobs in `crontab -e` |
| PHP-FPM | `env[CLAUDE_POOL_FRESH] = 600` in the pool config (`www.conf`), then restart PHP-FPM |
| Docker | `environment:` in `docker-compose.yml`, or `-e CLAUDE_POOL_FRESH=600` |
| One command | `CLAUDE_POOL_ACCOUNT=work claude-pool -p "hi"` |

### Running on a server

- **Same files everywhere:** every app has to use the same account folder and usage file. If your apps run as different users (for example `www-data` and your own user), put both in one group and point `CLAUDE_POOL_DIR` and `CLAUDE_POOL_STATE` at a folder that group can write.
- **Several servers:** they can share the same accounts. Each server checks real usage, so it sees what the others used.
- **Tokens:** `claude setup-token` works on any machine, so you can make the tokens on your laptop and copy the files to the server.

## Notes

Using several accounts to get around usage limits breaks Anthropic's terms.

MIT
