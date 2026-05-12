#! /usr/bin/env python
# Joel Rosdahl <joel@rosdahl.net>

import argparse
import functools
import html
import html.parser
import logging
import os
import re
import signal
import ssl
import stat
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import irc.bot
import irc.connection
import irc.strings
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import queries
from maxageset import MaxAgeSet


log = logging.getLogger("neo-bot")


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# Matches:
#   #123                       (uses defaults for user/repo)
#   user/repo#123              (explicit user and repo)
ISSUE_RE = re.compile(
    r"""
    (?:^|\s)                       # boundary must be space or start of line
    (?:
        (?P<user>[\w\.\-]+)        # user
        /
        (?P<repo>[\w\.\-]+)        # repo
    )?                             # optional; if absent, defaults are used
    \#(?P<num>[0-9]+)              # issue number
    \b                             # word boundary
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _mention_re(nickname):
    """Detect explicit nickname mentions like 'neo-bot:' or 'neo-bot,'."""
    return re.compile(
        r"(?:^|\s)" + re.escape(nickname) + r"[:,]?(?:\s|$)",
        re.IGNORECASE,
    )


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------

# Strip IRC control codes (CTCP \x01, colour \x03, formatting \x02 \x0F \x1D
# \x1F \x16, plus all C0 controls and DEL).  Without this, an attacker who
# controls an issue title can inject CTCPs, change colours, or smuggle
# CR/LF and break the IRC protocol.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_irc(text, max_len=300):
    """Make text safe to send over IRC."""
    if text is None:
        return ""
    cleaned = _CONTROL_CHARS_RE.sub(" ", str(text))
    return cleaned[:max_len]


def check_token_file_permissions(path):
    """Warn loudly if the token file is group- or world-accessible."""
    try:
        st = os.stat(path)
    except OSError as exc:
        log.warning("Cannot stat token file %s: %s", path, exc)
        return
    mode = stat.S_IMODE(st.st_mode)
    if mode & 0o077:
        log.warning(
            "Token file %s is accessible to other users (mode %o); "
            "run `chmod 600 %s` to fix.",
            path, mode, path,
        )


def resolve_token_path(cli_path):
    """Resolve the GitHub token path.

    If `--api-token-path` is given, use it.  Otherwise, when the process is
    started via systemd's `LoadCredential=`, fall back to
    `$CREDENTIALS_DIRECTORY/github_token`.
    """
    if cli_path:
        return cli_path
    creds_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if creds_dir:
        candidate = os.path.join(creds_dir, "github_token")
        if os.path.isfile(candidate):
            return candidate
    return None


# ---------------------------------------------------------------------------
# Emoji HTML parser (security fix #7: don't slice attacker-controlled HTML)
# ---------------------------------------------------------------------------


class _EmojiHTMLParser(html.parser.HTMLParser):
    """Extract textual content from GitHub's emojiHTML field."""

    def __init__(self):
        super().__init__()
        self._chunks = []

    def handle_data(self, data):
        self._chunks.append(data)

    def handle_entityref(self, name):
        self._chunks.append(html.unescape("&" + name + ";"))

    def handle_charref(self, name):
        self._chunks.append(html.unescape("&#" + name + ";"))

    def text(self):
        return "".join(self._chunks).strip()


def emoji_from_html(emoji_html):
    """Return the visible emoji from GitHub's emojiHTML field."""
    if not emoji_html:
        return ""
    parser = _EmojiHTMLParser()
    try:
        parser.feed(emoji_html)
        parser.close()
    except Exception:  # malformed HTML — treat as no emoji
        log.debug("Failed to parse emojiHTML: %r", emoji_html)
        return ""
    return sanitize_irc(parser.text(), max_len=16)


# ---------------------------------------------------------------------------
# Rate limiting (security fixes #5 and #6)
# ---------------------------------------------------------------------------


class RateLimiter:
    """Simple sliding-window rate limiter (per-key)."""

    def __init__(self, max_events, period_sec):
        self.max_events = max_events
        self.period_sec = period_sec
        self._events = defaultdict(deque)

    def allow(self, key):
        now = time.monotonic()
        cutoff = now - self.period_sec
        events = self._events[key]
        while events and events[0] < cutoff:
            events.popleft()
        if len(events) >= self.max_events:
            return False
        events.append(now)
        return True


# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------


class GitHubBot(irc.bot.SingleServerIRCBot):
    def __init__(
        self,
        api,
        channel,
        nickname,
        server,
        port,
        user,
        repo,
        max_age,
        cooldown_min,
        use_tls=True,
        sasl_login=None,
        sasl_password=None,
        per_user_lookups_per_min=10,
        per_channel_lookups_per_min=30,
        send_messages_per_sec=1,
        channels=None,
    ):
        connect_factory = self._make_connect_factory(use_tls, server)
        connect_kwargs = {"connect_factory": connect_factory}
        if sasl_login and sasl_password:
            connect_kwargs["sasl_login"] = sasl_login
            connect_kwargs["password"] = sasl_password
            log.info("SASL authentication enabled for %s", sasl_login)
        elif sasl_password and not sasl_login:
            connect_kwargs["password"] = sasl_password  # legacy server PASS
        super().__init__(
            ((server, port),), nickname, nickname, **connect_kwargs
        )
        self.api = api
        # Backward-compat: a single primary channel.  Multi-channel users
        # populate `channels` (robustness fix #13).
        self.channel = channel
        # Per-channel default (user, repo).  Channel keys are normalised to
        # lower case to match RFC 1459 / 2812 channel-name case folding.
        self._channels = {}
        if channels:
            for name, (u, r) in channels.items():
                self._channels[name.lower()] = (u, r)
        if channel and channel.lower() not in self._channels:
            self._channels[channel.lower()] = (user, repo)
        self.issue_re = ISSUE_RE
        self.user = user
        self.repo = repo
        self.max_age = timedelta(days=max_age)
        self.policies = [
            self.reject_if_too_old(),
            self.reject_if_repeated(timedelta(minutes=cooldown_min)),
        ]
        # Rate limiters (security fixes #5, #6)
        self._user_limiter = RateLimiter(per_user_lookups_per_min, 60)
        self._channel_limiter = RateLimiter(per_channel_lookups_per_min, 60)
        self._send_limiter = RateLimiter(send_messages_per_sec, 1)
        self._last_send_ts = 0.0
        self._send_min_interval = 1.0 / max(send_messages_per_sec, 1)

    def _defaults_for(self, channel_name):
        """Return (user, repo) defaults for the given channel."""
        if channel_name:
            entry = self._channels.get(channel_name.lower())
            if entry:
                return entry
        return (self.user, self.repo)

    @staticmethod
    def _make_connect_factory(use_tls, server):
        if not use_tls:
            log.warning(
                "TLS is DISABLED — credentials and traffic will be in clear text."
            )
            return irc.connection.Factory()
        ctx = ssl.create_default_context()
        wrapper = functools.partial(ctx.wrap_socket, server_hostname=server)
        return irc.connection.Factory(wrapper=wrapper)

    # ---- IRC events --------------------------------------------------------

    def on_nicknameinuse(self, c, e):
        c.nick(c.get_nickname() + "_")

    def on_welcome(self, c, e):
        for chan in self._channels:
            log.info("Joining %s", chan)
            c.join(chan)

    def on_privmsg(self, c, e):
        # Reply to the source nick, never leak private queries to the channel.
        return self._process_message(c, e.source.nick, e)

    def on_action(self, c, e):
        if e.target == c.get_nickname():
            respond_to = e.source.nick
        else:
            respond_to = e.target
        return self._process_message(c, respond_to, e)

    def on_pubmsg(self, c, e):
        return self._process_message(c, e.target, e)

    def on_kick(self, c, e):
        # Rejoin the channel we were kicked from (only if *we* were kicked).
        kicked_nick = e.arguments[0] if e.arguments else None
        chan = e.target or self.channel
        log.warning("Kicked from %s (target=%s): %s", chan, kicked_nick, e)
        if kicked_nick and kicked_nick != c.get_nickname():
            return
        if chan.lower() not in self._channels:
            return
        try:
            self.reactor.scheduler.execute_after(10, lambda: c.join(chan))
        except AttributeError:
            c.join(chan)

    def on_disconnect(self, c, e):
        # SingleServerIRCBot has built-in reconnection logic.
        log.warning("Disconnected: %s", e)

    # ---- Core processing ---------------------------------------------------

    def _apply_report_policies(self, msg, entity, nickname):
        for f in self.policies:
            resp = f(msg, entity, nickname)
            if resp is not None:
                return resp
        return None

    def _process_message(self, c, answer_to, e):
        nickname = c.get_nickname()
        source_nick = getattr(e.source, "nick", None) or "?"
        is_channel = e.target.startswith(("#", "&"))
        channel_key = e.target if is_channel else "<priv>"
        # Per-channel default user/repo (robustness fix #13).
        default_user, default_repo = self._defaults_for(
            e.target if is_channel else None
        )

        for msg in e.arguments:
            for user, repo, num in self.issue_re.findall(msg):
                # Per-user / per-channel GitHub lookup throttling (#5).
                if not self._user_limiter.allow(source_nick):
                    log.info("Rate-limit (user=%s) blocked lookup #%s",
                             source_nick, num)
                    continue
                if not self._channel_limiter.allow(channel_key):
                    log.info("Rate-limit (channel=%s) blocked lookup #%s",
                             channel_key, num)
                    continue

                try:
                    entity = self.find_entity_from_id(
                        num, user or default_user, repo or default_repo,
                    )
                except Exception as err:
                    log.exception("API failure for #%s: %s", num, err)
                    continue
                if entity is None:
                    log.info("Entity %s not found", num)
                    continue

                reject = self._apply_report_policies(msg, entity, nickname)
                if reject is not None:
                    log.info(reject)
                    continue

                self._send_throttled(c, answer_to, entity.render())

    def _send_throttled(self, c, target, text):
        """Throttle outgoing messages to avoid IRC server flood-kill (#6)."""
        text = sanitize_irc(text, max_len=400)
        now = time.monotonic()
        wait = self._last_send_ts + self._send_min_interval - now
        if wait > 0:
            try:
                self.reactor.scheduler.execute_after(
                    wait, lambda: self._do_send(c, target, text)
                )
                self._last_send_ts = now + wait
                return
            except AttributeError:
                time.sleep(min(wait, 0.5))
        self._do_send(c, target, text)
        self._last_send_ts = time.monotonic()

    def _do_send(self, c, target, text):
        c.privmsg(target, text)
        log.info("SENT [%s]: %s", target, text)

    # ---- Helpers -----------------------------------------------------------

    def find_entity_from_id(self, num, user=None, repo=None):
        if not user:
            user = self.user
        if not repo:
            repo = self.repo
        return self.api.find_by_id(num, user, repo)

    def reject_if_too_old(self):
        def validate(msg, entity, nickname):
            is_mention = bool(_mention_re(nickname).search(msg))
            is_old = (entity.date + self.max_age) <= datetime.now(timezone.utc)
            if is_old and not is_mention:
                return f"{entity.number} is too old"
            return None
        return validate

    def reject_if_repeated(self, period: timedelta):
        processed = MaxAgeSet(period)

        def validate(msg, entity, nickname):
            if entity.number in processed:
                return f"{entity.number} in cooldown period"
            processed.add(entity.number)
            return None
        return validate


# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------


@dataclass
class Issue:
    number: int
    user: str
    title: str
    url: str
    date: datetime

    def render(self):
        return f'Issue by @{sanitize_irc(self.user, 64)} "{sanitize_irc(self.title)}": {self.url}'


@dataclass
class PullRequest:
    number: int
    title: str
    url: str
    user: str
    date: datetime

    def render(self):
        return f'PR by @{sanitize_irc(self.user, 64)} "{sanitize_irc(self.title)}": {self.url}'


@dataclass
class Discussion:
    number: int
    title: str
    url: str
    user: str
    date: datetime
    num_comments: int
    category: str

    def render(self):
        comment_str = "comment" if self.num_comments == 1 else "comments"
        cat = sanitize_irc(self.category, 16)
        return (
            f'{cat} discussion by @{sanitize_irc(self.user, 64)} '
            f'"{sanitize_irc(self.title)}" '
            f"with {self.num_comments} {comment_str}: {self.url}"
        )


def _author_login(node):
    """Return the login of an entity's author, or 'ghost' if deleted."""
    author = node.get("author") if isinstance(node, dict) else None
    if not author:
        return "ghost"
    return author.get("login") or "ghost"


# ---------------------------------------------------------------------------
# GitHub API
# ---------------------------------------------------------------------------


class GraphQLError(Exception):
    """Raised when a GraphQL response contains an `errors` array."""


class GitHubAPI:
    _GRAPHQL_ENDPOINT = "https://api.github.com/graphql"

    def __init__(self, api_token_path, timeout_sec=10):
        check_token_file_permissions(api_token_path)
        api_key = self._load_api_key(api_token_path)
        self._session = self._init_session(api_key)
        self.timeout_sec = timeout_sec

    def _load_api_key(self, api_token_path):
        with open(api_token_path, "r") as fh:
            return fh.readline().strip()

    def _init_session(self, api_key):
        session = requests.Session()
        session.headers.update({"Authorization": f"Bearer {api_key}"})
        # Retry transient HTTP errors (robustness fix #3).
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    def query(self, query, variables=None):
        resp = self._session.post(
            self._GRAPHQL_ENDPOINT,
            json={"query": query, "variables": variables},
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        body = resp.json()
        # GraphQL returns 200 with an `errors` array on query problems
        # (robustness fix #1).
        errors = body.get("errors")
        if errors:
            messages = "; ".join(
                e.get("message", "?") for e in errors if isinstance(e, dict)
            )
            raise GraphQLError(messages or "unknown GraphQL error")
        return body

    def find_by_id(self, id_, user="neomutt", repo="neomutt"):
        variables = {"num": int(id_), "user": user, "repo": repo}
        res = self.query(queries.FETCH_ALL_BY_ID, variables)
        data = res["data"]["repository"]
        if issue := data["issue"]:
            return Issue(
                number=issue["number"],
                user=_author_login(issue),
                title=issue["title"],
                url=issue["url"],
                date=format_time(issue["createdAt"]),
            )
        elif pr := data["pullRequest"]:
            return PullRequest(
                number=pr["number"],
                user=_author_login(pr),
                title=pr["title"],
                url=pr["url"],
                date=format_time(pr["createdAt"]),
            )
        elif discussion := data["discussion"]:
            return Discussion(
                number=discussion["number"],
                user=_author_login(discussion),
                title=discussion["title"],
                url=discussion["url"],
                date=format_time(discussion["createdAt"]),
                num_comments=discussion["comments"]["totalCount"],
                category=emoji_from_html(discussion["category"]["emojiHTML"]),
            )
        return None

    # Backwards-compat shim
    def _emoji_from_emojiHTML(self, emojiHTML):
        return emoji_from_html(emojiHTML)


def format_time(time_str):
    """Parse a GitHub ISO-8601 UTC timestamp into a tz-aware datetime."""
    return datetime.strptime(time_str, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _read_secret_file(path):
    if not path:
        return None
    with open(path, "r") as fh:
        return fh.readline().strip()


def parse_args(argv=None):
    parser = argparse.ArgumentParser()

    parser.add_argument("server", help="IRC server to connect to")
    parser.add_argument("channel", help="IRC channel to join (e.g. #neomutt)")
    parser.add_argument("nickname", help="nickname to use")

    parser.add_argument(
        "-p", "--port", help="port of the IRC server (default: 6697 with TLS, 6667 without)",
        type=int, default=None,
    )
    parser.add_argument("-u", "--user", help="default github user", default="neomutt")
    parser.add_argument(
        "-r", "--repo", help="default github repository", default="neomutt"
    )
    parser.add_argument(
        "-m", "--max-age", "--max_age", dest="max_age",
        help="only show issues less than MAX_AGE days old",
        type=int, default=365,
    )

    parser.add_argument(
        "-k", "--api-token-path", "--api_token_path", dest="api_token_path",
        help="Path to file containing GH api key. "
             "Falls back to $CREDENTIALS_DIRECTORY/github_token (systemd).",
        required=False,
    )
    parser.add_argument(
        "--cooldown-min", "--cooldown_min", dest="cooldown_min",
        help="do not repeat lookups within the given number of minutes",
        type=int, default=5,
    )

    # Security: TLS + SASL
    tls_group = parser.add_mutually_exclusive_group()
    tls_group.add_argument(
        "--tls", dest="tls", action="store_true", default=True,
        help="use TLS to connect to the IRC server (default)",
    )
    tls_group.add_argument(
        "--no-tls", dest="tls", action="store_false",
        help="disable TLS (NOT recommended)",
    )
    parser.add_argument(
        "--sasl-user", dest="sasl_user", default=None,
        help="SASL PLAIN username for IRC authentication",
    )
    parser.add_argument(
        "--sasl-password-file", dest="sasl_password_file", default=None,
        help="path to file containing the SASL password",
    )

    # Rate limits
    parser.add_argument(
        "--per-user-lookups-per-min", type=int, default=10,
        help="max GitHub lookups per IRC user per minute",
    )
    parser.add_argument(
        "--per-channel-lookups-per-min", type=int, default=30,
        help="max GitHub lookups per channel per minute",
    )
    parser.add_argument(
        "--send-messages-per-sec", type=int, default=1,
        help="max messages per second sent to IRC (flood protection)",
    )

    parser.add_argument(
        "--channel", dest="extra_channels", action="append", default=[],
        metavar="NAME[:OWNER/REPO]",
        help="additional channel to join with optional default github "
             "owner/repo (may be repeated)",
    )

    parser.add_argument(
        "-v", "--verbose", action="store_true", help="enable debug logging"
    )

    return parser.parse_args(argv)


def parse_channel_spec(spec, default_user, default_repo):
    """Parse 'name[:owner/repo]' into (channel, user, repo)."""
    if ":" in spec:
        name, slug = spec.split(":", 1)
        if "/" not in slug:
            raise ValueError(f"invalid channel spec {spec!r}: expected NAME:OWNER/REPO")
        owner, repo = slug.split("/", 1)
    else:
        name = spec
        owner, repo = default_user, default_repo
    if not name.startswith(("#", "&")):
        name = "#" + name
    return name, owner, repo


def setup_logging(verbose=False):
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


def install_signal_handlers(bot):
    """Quit cleanly on SIGTERM / SIGINT (robustness fix #12)."""
    def handler(signum, frame):
        log.info("Signal %d received; sending QUIT and shutting down", signum)
        try:
            bot.disconnect("shutting down")
        except Exception:
            log.exception("Error while disconnecting")
        sys.exit(0)
    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def main():
    args = parse_args()
    setup_logging(args.verbose)
    log.debug("args: %s", args)

    token_path = resolve_token_path(args.api_token_path)
    if not token_path:
        log.error(
            "No GitHub token: pass --api-token-path or run via systemd "
            "with LoadCredential=github_token:..."
        )
        sys.exit(2)

    sasl_password = _read_secret_file(args.sasl_password_file)

    if args.port is None:
        port = 6697 if args.tls else 6667
    else:
        port = args.port

    primary_channel = (
        args.channel if args.channel.startswith(("#", "&")) else f"#{args.channel}"
    )
    channels = {primary_channel: (args.user, args.repo)}
    for spec in args.extra_channels:
        try:
            name, owner, repo = parse_channel_spec(spec, args.user, args.repo)
        except ValueError as exc:
            log.error("%s", exc)
            sys.exit(2)
        channels[name] = (owner, repo)

    api = GitHubAPI(token_path)
    bot = GitHubBot(
        api,
        primary_channel,
        args.nickname,
        args.server,
        port,
        args.user,
        args.repo,
        args.max_age,
        args.cooldown_min,
        use_tls=args.tls,
        sasl_login=args.sasl_user,
        sasl_password=sasl_password,
        channels=channels,
        per_user_lookups_per_min=args.per_user_lookups_per_min,
        per_channel_lookups_per_min=args.per_channel_lookups_per_min,
        send_messages_per_sec=args.send_messages_per_sec,
    )
    install_signal_handlers(bot)
    bot.start()


if __name__ == "__main__":
    main()
