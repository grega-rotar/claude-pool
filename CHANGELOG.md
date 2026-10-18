# Changelog

## 0.6.0

- **.env support:** settings and accounts can live in a `.env` file, the nearest one from the current folder up or `CLAUDE_POOL_ENV_FILE`. Only `CLAUDE_POOL_*` lines are read; a variable that is already set wins.
- **Accounts in variables:** `CLAUDE_POOL_TOKEN_<NAME>` and `CLAUDE_POOL_KEY_<NAME>` add accounts without files. They are taken out of the environment claude and alert commands run in.

## 0.5.0

- **Safe reruns:** a run cut off by a limit is rerun only if it used no tools yet, so nothing happens twice. `CLAUDE_POOL_RERUN=always|never` changes this.
- **Event log:** runs, switches, cut-offs and errors go to `events.log`, including the raw output of every failed run. `--pool-log` shows them.
- **Alerts:** `CLAUDE_POOL_ALERT` runs a command when every account is full, a token is refused, the CLI stops reporting usage, or a cut-off run isn't rerun. The same alert goes out at most once an hour.
- **Parallel runs:** past 75% of its 5-hour window (`CLAUDE_POOL_SPREAD_AT`), an account that's already busy gets no second run while another has room.
- **Faster checks:** a check times out after 15 s. Other callers wait at most 5 s for it, then go on with the saved numbers. A network error no longer marks a token as broken.
- **New commands:** `--pool-help`, `--pool-check`, `--pool-add`, `--pool-off`, `--pool-on`, `--pool-log`.
- **Warnings:** a token file that every user can read gets a warning. A user who can't read the account folder gets a clear message instead of a traceback.
- **Tests:** CI on Linux, macOS and Windows with Python 3.8-3.13, plus a test with two real users sharing one pool.

## 0.4.1

- Several users on one server: files in a shared folder are group-writable. A token file the user can't read is skipped.

## 0.4.0

- Windows support.

## 0.3.0

- Accounts whose numbers are older than 5 minutes are checked first, so use by other apps shows up.

## 0.2.0

- Drain: the account whose weekly window resets soonest is used first.

## 0.1.0

- First version.
