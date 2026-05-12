# neo-bot

neo-bot is an IRC bot who converts pull requests/issues number into links.

It is based on the testbot included with python's irc package.

## Dependencies

- Python 3.10+

## Install

```bash
$ pip install -r requirements.txt
```

## Usage

```
$ ./neo-bot.py --help
usage: neo-bot.py [-h] [-p PORT] [-u USER] [-r REPO] [-m MAX_AGE]
                  [-k API_TOKEN_PATH] [--cooldown-min COOLDOWN_MIN]
                  [--tls | --no-tls] [--sasl-user SASL_USER]
                  [--sasl-password-file SASL_PASSWORD_FILE]
                  [--per-user-lookups-per-min N]
                  [--per-channel-lookups-per-min N]
                  [--send-messages-per-sec N] [-v]
                  server channel nickname
```

### Required

- `server`, `channel`, `nickname`
- `-k, --api-token-path PATH` — file containing the GitHub API token
  (`public_repo` scope is enough).  Falls back to
  `$CREDENTIALS_DIRECTORY/github_token` when run via systemd
  `LoadCredential=`.

### Security options

- `--tls / --no-tls` — TLS is **on** by default; default port becomes 6697.
- `--sasl-user USER --sasl-password-file PATH` — authenticate to NickServ
  via SASL PLAIN (recommended on networks that support it, e.g. Libera).
- `--per-user-lookups-per-min`, `--per-channel-lookups-per-min`,
  `--send-messages-per-sec` — rate limits to protect both the GitHub API
  token and the IRC server connection.

The token file's permissions are checked on startup; warn if it's
group/world-accessible.  All issue titles, author logins and discussion
emoji are sanitized to strip control characters (CTCP, colour codes,
CRLF) before being sent to IRC.

### Example

```bash
./neo-bot.py --tls --sasl-user neo-bot --sasl-password-file /etc/neo-bot/sasl \
  -k /etc/neo-bot/github_token irc.libera.chat neomutt neo-bot
```

## systemd

The shipped [neo-bot.service](neo-bot.service) unit:

- Injects the GitHub token via `LoadCredential=` (no env var, no shared
  file path).
- Applies extensive sandboxing (`ProtectSystem=strict`,
  `NoNewPrivileges`, `MemoryDenyWriteExecute`, restricted syscalls and
  address families, dropped capabilities).
- Has a sensible restart back-off (`StartLimitBurst=5`,
  `StartLimitIntervalSec=60`).
- Does **not** auto-`git pull` on every restart — update the bot
  manually to pin to a vetted tag.

## Tests

```bash
python3 -m unittest test_neo_bot.py
```
