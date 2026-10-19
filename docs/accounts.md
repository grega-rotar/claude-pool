# Accounts

Each account is one file in `~/.config/claude-pool/`. The file name is the account's name, and the file holds only the token.

## Add one

```sh
claude setup-token              # log in to the account in the browser, copy the token
claude-pool --pool-add work     # paste it
```

Or by hand:

```sh
printf '%s\n' 'sk-ant-oat01-...' > ~/.config/claude-pool/work.token
chmod 600 ~/.config/claude-pool/work.token
```

Check it: `claude-pool --pool-check` should say `work: token works`.

It counts at once. Anything already running claude-pool picks it up on its next run.

- An API key goes in `work.key` instead of `work.token`.
- No file: put `CLAUDE_POOL_TOKEN_WORK=sk-ant-oat01-...` in the environment or a `.env` (`CLAUDE_POOL_KEY_WORK` for a key).

## Renew one

Tokens last a year. Make a new one with `claude setup-token` and write it over the old file. The name and usage stay.

## Take one out, put it back, remove it

| | Command | By hand |
|---|---|---|
| Out | `claude-pool --pool-off work` | `mv work.token work.token.off` |
| Back | `claude-pool --pool-on work` | `mv work.token.off work.token` |
| Gone | | `rm work.token.off` |

## "broken" in `--pool-status`

The token was refused. The message says why. Common ones:

- **"organization has disabled Claude subscription access"**: that account's org blocks it. Use another account.
- **expired or invalid**: renew it (above).
