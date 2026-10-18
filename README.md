# claude-pool

[![test](https://github.com/grega-rotar/claude-pool/actions/workflows/test.yml/badge.svg)](https://github.com/grega-rotar/claude-pool/actions/workflows/test.yml)

Runs `claude` on whichever of your accounts has usage left. It takes the same arguments as `claude` and prints the same output, so you only replace `claude` with `claude-pool`.

```sh
claude-pool -p "hi" --output-format json
```

Works on Linux, macOS and Windows. You need Python 3.8+ and the Claude Code CLI.

## Setup

**1. Install**

Linux / macOS:

```sh
curl -fsSL https://github.com/grega-rotar/claude-pool/releases/latest/download/claude_pool.py -o ~/.local/bin/claude-pool
chmod +x ~/.local/bin/claude-pool
```

Windows (PowerShell). This installs a `claude-pool` command:

```powershell
py -m pip install git+https://github.com/grega-rotar/claude-pool@v0.5.0
```

**2. Add accounts**

For each account, run `claude setup-token` and log in as that account. Then add the token it prints:

```sh
claude-pool --pool-add work        # paste the token when asked
```

The name (`work`) is yours to choose. The token is saved privately in `~/.config/claude-pool/work.token` (Windows: `%APPDATA%\claude-pool\`). API keys (`sk-ant-api...`) work too.

**3. Check**

```sh
claude-pool --pool-check
```

This tests claude, the folders and every token, and then shows each account's usage. The one marked `<- next` is used next.

## Commands

| Command | What it does |
|---|---|
| `claude-pool --pool-help` | All commands and settings |
| `claude-pool --pool-status` | Accounts and their usage |
| `claude-pool --pool-refresh` | Check every account's usage now |
| `claude-pool --pool-check` | Check the whole setup (exit 1 if something is wrong) |
| `claude-pool --pool-add <name>` | Add an account (or pipe the token: `echo "$TOKEN" \| claude-pool --pool-add work`) |
| `claude-pool --pool-off <name>` / `--pool-on <name>` | Take an account out / put it back |
| `claude-pool --pool-log [N]` | The last N events: runs, switches, errors |

Everything else goes straight to `claude`.

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
- **Picking:** it uses up the account whose week ends soonest, because leftover usage is lost at the reset. At 90% of the 5-hour limit, it moves to the next account. Past 75%, a busy account gets no second run while another has room. New accounts go last.
- **Limits:** if a run hits a limit before it used any tools, it starts over on the next account. If tools already ran (files edited, commands run), it stops and returns the error instead, so nothing happens twice.
- **Cost:** about 60 ms per call, plus 2-3 s when accounts are checked.

## Log and alerts

Every run, switch and error goes to `events.log`, next to the usage file. See it with `claude-pool --pool-log`.

To hear about problems, set `CLAUDE_POOL_ALERT` to a command. It runs when:

- every account is full,
- a token is refused,
- claude stops reporting usage (after a CLI update),
- a cut-off run isn't rerun.

The same alert goes out at most once an hour. The command gets `$CLAUDE_POOL_MESSAGE`, plus the event as JSON on stdin.

```sh
# Slack
export CLAUDE_POOL_ALERT='curl -s -X POST -H "Content-Type: application/json" -d "{\"text\": \"$CLAUDE_POOL_MESSAGE\"}" https://hooks.slack.com/services/...'
```

## Settings

You don't need to change anything. If you want to, set these as environment variables or in a `.env` file (see [In a .env file](#in-a-env-file)).

| Variable | Default | What it does |
|---|---|---|
| `CLAUDE_POOL_DIR` | see step 2 | Folder with the account files |
| `CLAUDE_POOL_STATE` | `~/.local/state/claude-pool/usage.json`, Windows `%LOCALAPPDATA%\claude-pool\usage.json` | Saved usage |
| `CLAUDE_POOL_BINARY` | `claude` | Path to the real Claude CLI |
| `CLAUDE_POOL_ACCOUNT` | none | Always use this one account |
| `CLAUDE_POOL_STRATEGY` | `drain` | `balance` uses the least-used account instead |
| `CLAUDE_POOL_SWITCH_AT` | `90` | 5-hour % at which it moves to the next account |
| `CLAUDE_POOL_SPREAD_AT` | `75` | 5-hour % at which a busy account gets no second run |
| `CLAUDE_POOL_FRESH` | `300` | Seconds between usage checks. `0` turns them off |
| `CLAUDE_POOL_LIMIT` | `100` | % at which an account counts as full |
| `CLAUDE_POOL_RERUN` | `safe` | After a limit cut-off: `safe` (only if no tools ran), `always` or `never` |
| `CLAUDE_POOL_ALERT` | none | Command to run on problems (see [Log and alerts](#log-and-alerts)) |
| `CLAUDE_POOL_LOG` | next to the usage file | Event log |
| `CLAUDE_POOL_ENV_FILE` | the nearest `.env` from the current folder up | `.env` file to read |
| `CLAUDE_POOL_TOKEN_<NAME>` | none | An account's OAuth token, instead of a `<name>.token` file |
| `CLAUDE_POOL_KEY_<NAME>` | none | An account's API key, instead of a `<name>.key` file |

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
| Laravel or any app with a `.env` | Add the lines to the app's `.env` (next section) |

### In a .env file

claude-pool reads the nearest `.env` from the folder it runs in, going up (so a Laravel app that runs it from `public/` still finds the app's `.env`). Set `CLAUDE_POOL_ENV_FILE` to read another file.

```sh
CLAUDE_POOL_TOKEN_WORK=sk-ant-oat01-...
CLAUDE_POOL_TOKEN_SPARE=sk-ant-oat01-...
CLAUDE_POOL_FRESH=600
```

- Only `CLAUDE_POOL_*` lines are read. The app's other secrets (`APP_KEY`, database passwords) are never passed to claude.
- `CLAUDE_POOL_TOKEN_<NAME>` adds account `<name>`: `CLAUDE_POOL_TOKEN_WORK_2` is `work-2`. The tokens are taken out of the environment claude runs in.
- A variable that is already set wins over the `.env`, and a `<name>.token` file wins over a variable with the same name.
- Laravel's `config:cache` doesn't matter: claude-pool reads the file itself.
- `claude-pool --pool-check` shows which `.env` it used and which accounts came from it.

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

**3. Add the tokens.** In a shared folder, `--pool-add` makes them readable by the group only (`640`):

```sh
sudo env CLAUDE_POOL_DIR=/etc/claude-pool claude-pool --pool-add work
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
sudo -u www-data env CLAUDE_POOL_DIR=/etc/claude-pool CLAUDE_POOL_STATE=/var/lib/claude-pool/usage.json claude-pool --pool-check
```

claude-pool makes the files it creates in a group-writable folder group-writable too (`660`), so every user can update them. A token file a user can't read is skipped with a warning. A user outside the group gets a clear error. This setup is tested with real users in CI (`tests/multiuser.sh`).

### Things to know

- **Every user can read every token** in the shared folder. Add only users you trust to the group.
- **claude needs a writable home folder.** It keeps its settings in `~/.claude.json` and `~/.claude/`. A service user like `www-data` often has a read-only home (`/var/www`). Give it a folder it can write with `CLAUDE_CONFIG_DIR=/var/lib/claude-pool/home-www-data`, or set `HOME` for that service.
- **`claude` must be on each user's `PATH`.** It's often installed in one user's `~/.local/bin`. Install it system-wide, or set `CLAUDE_POOL_BINARY=/full/path/to/claude`.
- **Docker:** mount `/etc/claude-pool` and `/var/lib/claude-pool` into the container, and run it with the group's ID (`group_add:` in compose).

## Several servers

Servers can share the same accounts. Each one keeps its own usage file, and the 5-minute check reads real usage, so every server sees what the others used. `claude setup-token` works on any machine, so make the tokens once and copy the files to each server.

## Tests

```sh
python3 -m unittest discover tests     # any OS, no account needed
sudo sh tests/multiuser.sh             # Linux, two real users
```

## Notes

Using several accounts to get around usage limits breaks Anthropic's terms.

MIT
