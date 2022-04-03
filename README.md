# neo-bot

neo-bot is an IRC bot who converts pull requests/issues number into links.

It is based on the testbot included with python's irc package.

## Dependencies

- Python 3

## Install

```bash
$ pip install -r requirements.txt
```

## Usage

```
$ ./neo-bot.py --help
usage: neo-bot.py [-h] [-p PORT] [-u USER] [-r REPO] [-m MAX_AGE] server channel nickname

positional arguments:
  server                IRC server to connect to
  channel               IRC channel to join
  nickname              nickname to use

required arguments:
  -k PATH, --api_token_path PATH Path to file containing the GitHub api key

optional arguments:
  -h, --help            show this help message and exit
  -p PORT, --port PORT  port of the IRC server
  -u USER, --user USER  default github user
  -r REPO, --repo REPO  default github repository
  -m MAX_AGE, --max_age MAX_AGE
                        only show issues less than MAX_AGE days old
```

### Example:

```bash
./neo-bot.py irc.libera.chat neomutt neo-bot
```
