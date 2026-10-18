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

## Several users on one server

Each Linux user has its own home folder, so by default each user has its own accounts and its own usage file. A web app often runs as `www-data`, a script as `deploy`, and you as yourself. Pick one of two setups.

### Option A: each user on its own (simplest)

Do steps 1-2 of [Setup](#setup) for each user. They can use the same tokens.

- **Good:** nothing to share, no permissions to set up.
- **Downside:** each user knows only its own runs. It sees what the others used only at the 5-minute check. In between, two users can pick the same account, and that's fine unless the account is nearly full.

### Option B: one shared pool (recommended for busy servers)

All users read one account folder and write one usage file, so each one sees the others' usage right away.

**1. Make a group and add every user that runs claude-pool:**

```sh
sudo groupadd claude-pool
sudo usermod -aG claude-pool www-data
sudo usermod -aG claude-pool deploy
```

A user has to log in again (or restart its service) before the new group takes effect.

**2. Make the shared folders.** `2770` means the group can read and write, others get nothing, and new files belong to the group:

```sh
sudo install -d -m 2770 -g claude-pool /etc/claude-pool        # accounts
sudo install -d -m 2770 -g claude-pool /var/lib/claude-pool    # usage
```

**3. Add the tokens.** They should be readable by the group only (`640`):

```sh
sudo sh -c 'echo "sk-ant-oat01-..." > /etc/claude-pool/work.token'
sudo chgrp claude-pool /etc/claude-pool/*.token
sudo chmod 640 /etc/claude-pool/*.token
```

**4. Point every user at those folders.** Set these two variables wherever each app runs (see [Where to set them](#where-to-set-them)):

```sh
CLAUDE_POOL_DIR=/etc/claude-pool
CLAUDE_POOL_STATE=/var/lib/claude-pool/usage.json
```

For example, for a PHP app as `www-data`, add them to the PHP-FPM pool config:

```ini
env[CLAUDE_POOL_DIR] = /etc/claude-pool
env[CLAUDE_POOL_STATE] = /var/lib/claude-pool/usage.json
```

**5. Check as each user:**

```sh
sudo -u www-data env CLAUDE_POOL_DIR=/etc/claude-pool CLAUDE_POOL_STATE=/var/lib/claude-pool/usage.json claude-pool --pool-status
```

claude-pool makes the files it creates in a group-writable folder group-writable too (`660`), so every user can update them. A token file a user can't read is skipped with a warning.

### Things to know

- **Every user can read every token** in the shared folder. Add only users you trust to the group.
- **claude needs a writable home folder.** It keeps its settings in `~/.claude.json` and `~/.claude/`. A service user like `www-data` often has a read-only home (`/var/www`). Give it a folder it can write with `CLAUDE_CONFIG_DIR=/var/lib/claude-pool/home-www-data`, or set `HOME` for that service.
- **`claude` must be on each user's `PATH`.** It's often installed in one user's `~/.local/bin`. Install it system-wide, or set `CLAUDE_POOL_BINARY=/full/path/to/claude`.
- **Docker:** mount `/etc/claude-pool` and `/var/lib/claude-pool` into the container, and run it with the group's ID (`group_add:` in compose).

## Several servers

Servers can share the same accounts. Each one keeps its own usage file, and the 5-minute check reads real usage, so every server sees what the others used. `claude setup-token` works on any machine, so make the tokens once and copy the files to each server.

## Notes

Using several accounts to get around usage limits breaks Anthropic's terms.

MIT
