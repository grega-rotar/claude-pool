# Servers

## One server, one user

Same as on your laptop: install it and add accounts as that user. A systemd service or cron job that calls `claude-pool` instead of `claude` needs nothing else. New token files count on the next run, so no restart.

If claude-pool isn't on the service's `PATH`, call it by its full path: `~/.local/bin/claude-pool`.

## One pool for several users

So `www-data`, `deploy` and you share the same accounts and usage:

```sh
sudo groupadd claude-pool
sudo usermod -aG claude-pool www-data
sudo install -d -m 2770 -g claude-pool /etc/claude-pool /var/lib/claude-pool
sudo env CLAUDE_POOL_DIR=/etc/claude-pool claude-pool --pool-add work
```

Give every user:

```sh
CLAUDE_POOL_DIR=/etc/claude-pool
CLAUDE_POOL_STATE=/var/lib/claude-pool/usage.json
```

Then check as each user: `sudo -u www-data claude-pool --pool-check`.

- Every user in the group can read every token.
- Service users need a writable home for claude: set `CLAUDE_CONFIG_DIR` or `HOME`.
- claude must be on their `PATH`, or set `CLAUDE_POOL_BINARY`.

## Several servers

Copy the same token files to each server. Each sees the others' use at the 5-minute check.

## Windows

Install it as the README says. A Windows service user needs Python and Claude Code on its own `PATH`.
