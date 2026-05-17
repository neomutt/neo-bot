"""Tests for neo-bot bug fixes and security hardening."""

import datetime
import importlib
import os
import stat
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import MagicMock, patch


def _install_irc_stub():
    """The `irc` package is a runtime-only dependency; stub it for tests."""
    if "irc" in sys.modules:
        return

    irc_pkg = types.ModuleType("irc")

    irc_bot = types.ModuleType("irc.bot")

    class _SingleServerIRCBot:
        def __init__(self, *args, **kwargs):
            self.reactor = MagicMock()
            self._init_args = args
            self._init_kwargs = kwargs

        def start(self):
            pass

    irc_bot.SingleServerIRCBot = _SingleServerIRCBot

    irc_strings = types.ModuleType("irc.strings")

    irc_connection = types.ModuleType("irc.connection")

    class _Factory:
        def __init__(self, wrapper=None, **kwargs):
            self.wrapper = wrapper
            self.kwargs = kwargs

    irc_connection.Factory = _Factory

    irc_pkg.bot = irc_bot
    irc_pkg.strings = irc_strings
    irc_pkg.connection = irc_connection

    sys.modules["irc"] = irc_pkg
    sys.modules["irc.bot"] = irc_bot
    sys.modules["irc.strings"] = irc_strings
    sys.modules["irc.connection"] = irc_connection


_install_irc_stub()

# Import neo-bot.py (filename has a hyphen, so use importlib).
spec = importlib.util.spec_from_file_location("neo_bot", "neo-bot.py")
neo_bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(neo_bot)

from maxageset import MaxAgeSet  # noqa: E402


# ---------------------------------------------------------------------------
# MaxAgeSet — bugs #2, #3, #4
# ---------------------------------------------------------------------------
class TestMaxAgeSet(unittest.TestCase):
    def test_add_and_contains(self):
        s = MaxAgeSet(datetime.timedelta(seconds=60))
        s.add(42)
        self.assertIn(42, s)
        self.assertNotIn(99, s)

    def test_add_dedupes(self):
        s = MaxAgeSet(datetime.timedelta(seconds=60))
        s.add("x")
        s.add("x")
        self.assertEqual(len(s), 1)

    def test_iter_yields_items(self):
        """Bug #3: __iter__ called self._items() (not callable)."""
        s = MaxAgeSet(datetime.timedelta(seconds=60))
        s.add("a")
        s.add("b")
        self.assertEqual(sorted(list(s)), ["a", "b"])

    def test_discard_removes_item(self):
        """Bug #2: discard had broken unpacking and indexing."""
        s = MaxAgeSet(datetime.timedelta(seconds=60))
        s.add("a")
        s.add("b")
        s.add("c")
        s.discard("b")
        self.assertNotIn("b", s)
        self.assertIn("a", s)
        self.assertIn("c", s)

    def test_discard_missing_is_noop(self):
        s = MaxAgeSet(datetime.timedelta(seconds=60))
        s.add("a")
        s.discard("missing")  # must not raise
        self.assertIn("a", s)

    def test_cleanup_expires_old_items(self):
        """Bug #4: cleanup must drop items older than max_age."""
        s = MaxAgeSet(datetime.timedelta(milliseconds=50))
        s.add("old")
        time.sleep(0.1)
        s.add("new")
        self.assertNotIn("old", s)
        self.assertIn("new", s)
        self.assertEqual(len(s), 1)

    def test_iter_after_expiry(self):
        s = MaxAgeSet(datetime.timedelta(milliseconds=50))
        s.add("old")
        time.sleep(0.1)
        s.add("new")
        self.assertEqual(list(s), ["new"])

    def test_construct_from_iterable(self):
        s = MaxAgeSet(datetime.timedelta(seconds=60), iterable=[1, 2, 3])
        self.assertEqual(len(s), 3)


# ---------------------------------------------------------------------------
# ISSUE_RE — bug #7
# ---------------------------------------------------------------------------
class TestIssueRegex(unittest.TestCase):
    def _findall(self, text):
        return neo_bot.ISSUE_RE.findall(text)

    def test_bare_number(self):
        self.assertEqual(self._findall("see #123"), [("", "", "123")])

    def test_user_repo(self):
        self.assertEqual(
            self._findall("see neomutt/neomutt#42"),
            [("neomutt", "neomutt", "42")],
        )

    def test_no_match_at_start_of_word(self):
        # "v2.0#3" must not match — version-like prefixes used to trigger
        # the old regex via the `repo` group.
        self.assertEqual(self._findall("released v2.0#3 today"), [])

    def test_no_match_bare_repo(self):
        # `repo#5` (no user/) must not match.
        self.assertEqual(self._findall("look at neomutt#5"), [])

    def test_match_at_start_of_line(self):
        self.assertEqual(self._findall("#7 is fixed"), [("", "", "7")])

    def test_multiple_matches(self):
        result = self._findall("fixed #1 and neomutt/neomutt#2")
        self.assertEqual(
            result, [("", "", "1"), ("neomutt", "neomutt", "2")]
        )

    def test_no_match_in_url(self):
        # "github.com/foo/bar#3" — preceded by non-space; should not match
        # as a github lookup.
        self.assertEqual(
            self._findall("https://example.com/foo/bar#3"), []
        )


# ---------------------------------------------------------------------------
# Mention regex — bug #5
# ---------------------------------------------------------------------------
class TestMentionRegex(unittest.TestCase):
    def test_colon_mention(self):
        r = neo_bot._mention_re("neo-bot")
        self.assertTrue(r.search("neo-bot: hello #1"))

    def test_comma_mention(self):
        r = neo_bot._mention_re("neo-bot")
        self.assertTrue(r.search("neo-bot, please look at #1"))

    def test_bare_mention(self):
        r = neo_bot._mention_re("neo-bot")
        self.assertTrue(r.search("hey neo-bot what about #1"))

    def test_substring_no_match(self):
        r = neo_bot._mention_re("neo")
        self.assertFalse(r.search("neologism #1"))

    def test_case_insensitive(self):
        r = neo_bot._mention_re("neo-bot")
        self.assertTrue(r.search("NEO-BOT: #1"))


# ---------------------------------------------------------------------------
# format_time — bug #9 (tz-aware UTC)
# ---------------------------------------------------------------------------
class TestFormatTime(unittest.TestCase):
    def test_returns_utc_aware(self):
        dt = neo_bot.format_time("2024-01-02T03:04:05Z")
        self.assertEqual(dt.tzinfo, datetime.timezone.utc)
        self.assertEqual(
            dt,
            datetime.datetime(2024, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc),
        )

    def test_comparable_to_now_utc(self):
        dt = neo_bot.format_time("2024-01-02T03:04:05Z")
        # Must not raise (would have raised before fix #9 on a naive vs aware
        # comparison if the rest of the code uses tz-aware now()).
        delta = datetime.datetime.now(datetime.timezone.utc) - dt
        self.assertIsInstance(delta, datetime.timedelta)


# ---------------------------------------------------------------------------
# _author_login — bug #8 (deleted authors)
# ---------------------------------------------------------------------------
class TestAuthorLogin(unittest.TestCase):
    def test_normal_author(self):
        self.assertEqual(
            neo_bot._author_login({"author": {"login": "alice"}}), "alice"
        )

    def test_null_author(self):
        self.assertEqual(neo_bot._author_login({"author": None}), "ghost")

    def test_missing_author(self):
        self.assertEqual(neo_bot._author_login({}), "ghost")

    def test_author_with_null_login(self):
        self.assertEqual(
            neo_bot._author_login({"author": {"login": None}}), "ghost"
        )


# ---------------------------------------------------------------------------
# GitHubAPI._init_session — bug #1
# ---------------------------------------------------------------------------
class TestSessionInit(unittest.TestCase):
    def test_session_is_returned_and_assigned(self):
        api = neo_bot.GitHubAPI.__new__(neo_bot.GitHubAPI)
        session = api._init_session("token-xyz")
        # Must return a Session (not None).
        self.assertIsNotNone(session)
        self.assertEqual(
            session.headers.get("Authorization"), "Bearer token-xyz"
        )


# ---------------------------------------------------------------------------
# find_by_id — integrates _author_login + format_time on a deleted user
# ---------------------------------------------------------------------------
class TestFindById(unittest.TestCase):
    def _make_api(self):
        api = neo_bot.GitHubAPI.__new__(neo_bot.GitHubAPI)
        api._session = MagicMock()
        api.timeout_sec = 5
        return api

    def test_issue_with_deleted_author(self):
        api = self._make_api()
        api.query = MagicMock(
            return_value={
                "data": {
                    "repository": {
                        "issue": {
                            "number": 1,
                            "title": "broken",
                            "url": "https://example.com/1",
                            "createdAt": "2024-01-01T00:00:00Z",
                            "author": None,
                        },
                        "pullRequest": None,
                        "discussion": None,
                    }
                }
            }
        )
        entity = api.find_by_id(1, "neomutt", "neomutt")
        self.assertEqual(entity.user, "ghost")
        self.assertEqual(entity.date.tzinfo, datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Bot behavior — bugs #5, #6, #10
# ---------------------------------------------------------------------------
class TestBotBehavior(unittest.TestCase):
    def _make_bot(self):
        api = MagicMock()
        # Bypass SingleServerIRCBot.__init__ side effects.
        bot = neo_bot.GitHubBot.__new__(neo_bot.GitHubBot)
        bot.api = api
        bot.channel = "#neomutt"
        bot.user = "neomutt"
        bot.repo = "neomutt"
        bot.max_age = datetime.timedelta(days=365)
        bot.issue_re = neo_bot.ISSUE_RE
        bot.policies = [
            bot.reject_if_too_old(),
            bot.reject_if_repeated(datetime.timedelta(minutes=5)),
        ]
        bot.reactor = MagicMock()
        # Permissive limiters so the bug-fix tests are unaffected.
        bot._user_limiter = neo_bot.RateLimiter(1000, 60)
        bot._channel_limiter = neo_bot.RateLimiter(1000, 60)
        bot._send_limiter = neo_bot.RateLimiter(1000, 1)
        bot._last_send_ts = 0.0
        bot._send_min_interval = 0.0
        # Multi-channel default registry (robustness fix #13).
        bot._channels = {"#neomutt": ("neomutt", "neomutt")}
        return bot

    def test_privmsg_replies_to_primary_channel(self):
        """Private messages are echoed to the configured channel."""
        bot = self._make_bot()
        bot.api.find_by_id.return_value = neo_bot.Issue(
            number=1,
            user="alice",
            title="hi",
            url="https://example.com/1",
            date=datetime.datetime.now(datetime.timezone.utc),
        )
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        e = MagicMock()
        e.arguments = ["#1"]
        e.source.nick = "alice"
        e.target = "neo-bot"

        bot.on_privmsg(c, e)
        c.privmsg.assert_called_once()
        target, _ = c.privmsg.call_args[0]
        self.assertEqual(target, "#neomutt")

    def test_private_action_replies_to_primary_channel(self):
        """Direct CTCP ACTION lookups are echoed to the configured channel."""
        bot = self._make_bot()
        bot.api.find_by_id.return_value = neo_bot.Issue(
            number=1,
            user="alice",
            title="hi",
            url="https://example.com/1",
            date=datetime.datetime.now(datetime.timezone.utc),
        )
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        e = MagicMock()
        e.arguments = ["#1"]
        e.source.nick = "alice"
        e.target = "neo-bot"

        bot.on_action(c, e)
        c.privmsg.assert_called_once()
        target, _ = c.privmsg.call_args[0]
        self.assertEqual(target, "#neomutt")

    def test_too_old_blocked_without_mention(self):
        """Bug #5/#9: aged issues without explicit mention are rejected."""
        bot = self._make_bot()
        bot.max_age = datetime.timedelta(days=1)
        bot.policies = [bot.reject_if_too_old()]
        old_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        bot.api.find_by_id.return_value = neo_bot.Issue(
            number=1, user="alice", title="hi",
            url="https://example.com/1", date=old_date,
        )
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        e = MagicMock()
        e.arguments = ["look at #1"]
        e.source.nick = "alice"
        e.target = "#neomutt"

        bot.on_pubmsg(c, e)
        c.privmsg.assert_not_called()

    def test_too_old_allowed_with_mention(self):
        """Bug #5: explicit mention bypasses the max-age policy."""
        bot = self._make_bot()
        bot.max_age = datetime.timedelta(days=1)
        bot.policies = [bot.reject_if_too_old()]
        old_date = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
        bot.api.find_by_id.return_value = neo_bot.Issue(
            number=1, user="alice", title="hi",
            url="https://example.com/1", date=old_date,
        )
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        e = MagicMock()
        e.arguments = ["neo-bot: please look at #1"]
        e.source.nick = "alice"
        e.target = "#neomutt"

        bot.on_pubmsg(c, e)
        c.privmsg.assert_called_once()

    def test_kick_uses_scheduler_no_blocking_loop(self):
        """Bug #10: on_kick must not block; it should schedule a rejoin."""
        bot = self._make_bot()
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        e = MagicMock()
        e.target = "#neomutt"
        e.arguments = ["neo-bot", "bye"]
        start = time.monotonic()
        bot.on_kick(c, e)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.0, "on_kick must not block")
        bot.reactor.scheduler.execute_after.assert_called_once()

    def test_disconnect_no_blocking_loop(self):
        """Bug #10: on_disconnect must not block in a manual reconnect loop."""
        bot = self._make_bot()
        c = MagicMock()
        e = MagicMock()
        start = time.monotonic()
        bot.on_disconnect(c, e)
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 1.0, "on_disconnect must not block")


# ---------------------------------------------------------------------------
# Security #7: emoji HTML safe parser
# ---------------------------------------------------------------------------
class TestEmojiHTML(unittest.TestCase):
    def test_basic_emoji(self):
        self.assertEqual(neo_bot.emoji_from_html("<div>🔧</div>"), "🔧")

    def test_html_entity(self):
        self.assertEqual(neo_bot.emoji_from_html("<div>&amp;</div>"), "&")

    def test_numeric_entity(self):
        self.assertEqual(neo_bot.emoji_from_html("<div>&#128295;</div>"), "🔧")

    def test_strips_nested_tags(self):
        # Hostile / unexpected HTML must not crash and must not leak markup.
        result = neo_bot.emoji_from_html("<div><span>x</span></div>")
        self.assertEqual(result, "x")

    def test_strips_control_chars(self):
        result = neo_bot.emoji_from_html("<div>\x01\x02ok</div>")
        self.assertNotIn("\x01", result)
        self.assertIn("ok", result)

    def test_empty(self):
        self.assertEqual(neo_bot.emoji_from_html(""), "")
        self.assertEqual(neo_bot.emoji_from_html(None), "")

    def test_malformed_does_not_raise(self):
        # Should never raise, even on garbage input.
        try:
            neo_bot.emoji_from_html("<div<<>>nope")
        except Exception as exc:  # pragma: no cover
            self.fail(f"emoji_from_html raised on garbage: {exc}")


# ---------------------------------------------------------------------------
# Security #8: IRC sanitization
# ---------------------------------------------------------------------------
class TestSanitizeIRC(unittest.TestCase):
    def test_strips_ctcp(self):
        self.assertNotIn("\x01", neo_bot.sanitize_irc("\x01ACTION evil\x01"))

    def test_strips_color_codes(self):
        self.assertNotIn("\x03", neo_bot.sanitize_irc("\x0304red text\x03"))

    def test_strips_crlf(self):
        out = neo_bot.sanitize_irc("hi\r\nPRIVMSG #other :pwn")
        self.assertNotIn("\r", out)
        self.assertNotIn("\n", out)
        self.assertNotIn("PRIVMSG", out.split(" ")[0])  # no command injection

    def test_strips_del(self):
        self.assertNotIn("\x7f", neo_bot.sanitize_irc("a\x7fb"))

    def test_truncates(self):
        self.assertEqual(len(neo_bot.sanitize_irc("x" * 1000, max_len=100)), 100)

    def test_none_safe(self):
        self.assertEqual(neo_bot.sanitize_irc(None), "")

    def test_render_sanitizes_title(self):
        issue = neo_bot.Issue(
            number=1, user="alice",
            title="evil\x01\r\nPRIVMSG #x :pwn",
            url="https://example.com/1",
            date=datetime.datetime.now(datetime.timezone.utc),
        )
        rendered = issue.render()
        for bad in ("\x01", "\r", "\n"):
            self.assertNotIn(bad, rendered)


# ---------------------------------------------------------------------------
# Security #2: token file permission check
# ---------------------------------------------------------------------------
class TestTokenFilePermissions(unittest.TestCase):
    def test_warns_on_world_readable(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("secret")
            path = fh.name
        try:
            os.chmod(path, 0o644)
            with self.assertLogs(neo_bot.log, level="WARNING") as cm:
                neo_bot.check_token_file_permissions(path)
            self.assertTrue(
                any("accessible to other users" in m for m in cm.output)
            )
        finally:
            os.unlink(path)

    def test_quiet_on_secure_perms(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("secret")
            path = fh.name
        try:
            os.chmod(path, 0o600)
            # Capture WARNING+; must not emit a permission warning.
            with self.assertLogs(neo_bot.log, level="WARNING") as cm:
                # Need at least one log record for assertLogs not to fail,
                # so log a sentinel.
                neo_bot.log.warning("sentinel")
                neo_bot.check_token_file_permissions(path)
            warnings = [m for m in cm.output if "accessible to other" in m]
            self.assertEqual(warnings, [])
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# Security #10: systemd LoadCredential fallback
# ---------------------------------------------------------------------------
class TestResolveTokenPath(unittest.TestCase):
    def test_explicit_wins(self):
        self.assertEqual(neo_bot.resolve_token_path("/explicit"), "/explicit")

    def test_credentials_directory_fallback(self):
        with tempfile.TemporaryDirectory() as td:
            token = os.path.join(td, "github_token")
            with open(token, "w") as fh:
                fh.write("xyz")
            with patch.dict(os.environ, {"CREDENTIALS_DIRECTORY": td}):
                self.assertEqual(neo_bot.resolve_token_path(None), token)

    def test_no_credentials_returns_none(self):
        env = {k: v for k, v in os.environ.items() if k != "CREDENTIALS_DIRECTORY"}
        with patch.dict(os.environ, env, clear=True):
            self.assertIsNone(neo_bot.resolve_token_path(None))


# ---------------------------------------------------------------------------
# Security #5/#6: rate limiters
# ---------------------------------------------------------------------------
class TestRateLimiter(unittest.TestCase):
    def test_allows_up_to_limit(self):
        rl = neo_bot.RateLimiter(3, 60)
        self.assertTrue(rl.allow("k"))
        self.assertTrue(rl.allow("k"))
        self.assertTrue(rl.allow("k"))
        self.assertFalse(rl.allow("k"))

    def test_independent_keys(self):
        rl = neo_bot.RateLimiter(1, 60)
        self.assertTrue(rl.allow("a"))
        self.assertFalse(rl.allow("a"))
        self.assertTrue(rl.allow("b"))

    def test_window_expires(self):
        rl = neo_bot.RateLimiter(1, 0.05)
        self.assertTrue(rl.allow("k"))
        self.assertFalse(rl.allow("k"))
        time.sleep(0.1)
        self.assertTrue(rl.allow("k"))


class TestBotRateLimiting(TestBotBehavior):
    """Inherits _make_bot helper from TestBotBehavior."""

    def test_per_user_lookup_limit_blocks(self):
        bot = self._make_bot()
        bot._user_limiter = neo_bot.RateLimiter(1, 60)
        bot.api.find_by_id.return_value = neo_bot.Issue(
            number=1, user="alice", title="hi",
            url="https://example.com/1",
            date=datetime.datetime.now(datetime.timezone.utc),
        )
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        e1 = MagicMock(); e1.arguments = ["#1"]
        e1.source.nick = "alice"; e1.target = "#neomutt"
        e2 = MagicMock(); e2.arguments = ["#2"]
        e2.source.nick = "alice"; e2.target = "#neomutt"
        bot.on_pubmsg(c, e1)
        bot.on_pubmsg(c, e2)
        # First call sent, second blocked by per-user limiter.
        self.assertEqual(c.privmsg.call_count, 1)

    def test_send_throttle_uses_scheduler_when_due(self):
        bot = self._make_bot()
        bot._send_min_interval = 5.0
        bot._last_send_ts = time.monotonic()  # next send must be deferred
        bot.api.find_by_id.return_value = neo_bot.Issue(
            number=1, user="alice", title="hi",
            url="https://example.com/1",
            date=datetime.datetime.now(datetime.timezone.utc),
        )
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        e = MagicMock()
        e.arguments = ["#1"]
        e.source.nick = "alice"
        e.target = "#neomutt"
        bot.on_pubmsg(c, e)
        # Scheduler used; immediate privmsg NOT called.
        c.privmsg.assert_not_called()
        bot.reactor.scheduler.execute_after.assert_called()


# ---------------------------------------------------------------------------
# Security #1: TLS / SASL connection wiring
# ---------------------------------------------------------------------------
class TestTLSAndSASL(unittest.TestCase):
    def test_tls_factory_built(self):
        api = MagicMock()
        bot = neo_bot.GitHubBot(
            api, "#c", "n", "irc.example.org", 6697,
            "u", "r", 365, 5, use_tls=True,
        )
        kwargs = bot._init_kwargs
        self.assertIn("connect_factory", kwargs)
        self.assertIsNotNone(kwargs["connect_factory"].wrapper)
        self.assertNotIn("sasl_login", kwargs)

    def test_no_tls_skips_wrapper(self):
        api = MagicMock()
        bot = neo_bot.GitHubBot(
            api, "#c", "n", "irc.example.org", 6667,
            "u", "r", 365, 5, use_tls=False,
        )
        kwargs = bot._init_kwargs
        self.assertIsNone(kwargs["connect_factory"].wrapper)

    def test_sasl_credentials_passed(self):
        api = MagicMock()
        bot = neo_bot.GitHubBot(
            api, "#c", "n", "irc.example.org", 6697,
            "u", "r", 365, 5, use_tls=True,
            sasl_login="botuser", sasl_password="pw-test-value",
        )
        kwargs = bot._init_kwargs
        self.assertEqual(kwargs["sasl_login"], "botuser")
        self.assertEqual(kwargs["password"], "pw-test-value")


# ---------------------------------------------------------------------------
# Security #9: logging used (not bare print) for events
# ---------------------------------------------------------------------------
class TestLogging(unittest.TestCase):
    def test_kick_logs_warning(self):
        bot = neo_bot.GitHubBot.__new__(neo_bot.GitHubBot)
        bot.channel = "#x"
        bot.reactor = MagicMock()
        c = MagicMock()
        e = MagicMock()
        with self.assertLogs(neo_bot.log, level="WARNING") as cm:
            bot.on_kick(c, e)
        self.assertTrue(any("Kicked" in m for m in cm.output))

    def test_disconnect_logs_warning(self):
        bot = neo_bot.GitHubBot.__new__(neo_bot.GitHubBot)
        c = MagicMock(); e = MagicMock()
        with self.assertLogs(neo_bot.log, level="WARNING") as cm:
            bot.on_disconnect(c, e)
        self.assertTrue(any("Disconnected" in m for m in cm.output))


# ---------------------------------------------------------------------------
# CLI: TLS default, port defaulting, --no-tls flag
# ---------------------------------------------------------------------------
class TestCLI(unittest.TestCase):
    def test_tls_defaults_on(self):
        args = neo_bot.parse_args(["s", "c", "n", "-k", "/tmp/tok"])
        self.assertTrue(args.tls)

    def test_no_tls_flag(self):
        args = neo_bot.parse_args(["s", "c", "n", "-k", "/tmp/tok", "--no-tls"])
        self.assertFalse(args.tls)

    def test_legacy_arg_names_still_work(self):
        # Pre-existing --max_age and --cooldown_min must keep working.
        args = neo_bot.parse_args([
            "s", "c", "n", "-k", "/tmp/tok",
            "--max_age", "30", "--cooldown_min", "7",
        ])
        self.assertEqual(args.max_age, 30)
        self.assertEqual(args.cooldown_min, 7)


# ---------------------------------------------------------------------------
# Systemd unit hardening (#3, #4, #10)
# ---------------------------------------------------------------------------
class TestSystemdUnit(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(os.path.dirname(__file__), "neo-bot.service")) as fh:
            self.unit = fh.read()

    def test_no_git_pull_in_execstartpre(self):
        # Look for an ExecStartPre directive that runs `git pull`.
        for line in self.unit.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            self.assertNotIn(
                "git pull", stripped,
                f"unit must not auto-update via git pull: {line}",
            )

    def test_start_limit_set(self):
        self.assertIn("StartLimitBurst", self.unit)
        self.assertIn("StartLimitIntervalSec", self.unit)

    def test_load_credential(self):
        self.assertIn("LoadCredential=", self.unit)

    def test_hardening_directives(self):
        for directive in (
            "NoNewPrivileges=true",
            "ProtectSystem=strict",
            "PrivateTmp=true",
            "RestrictAddressFamilies=",
            "CapabilityBoundingSet=",
            "SystemCallFilter=",
            "MemoryDenyWriteExecute=true",
        ):
            self.assertIn(directive, self.unit, f"missing: {directive}")


# ---------------------------------------------------------------------------
# Robustness #1: GraphQL `errors` array surfaces as an exception
# ---------------------------------------------------------------------------
class TestGraphQLErrors(unittest.TestCase):
    def _make_api(self):
        api = neo_bot.GitHubAPI.__new__(neo_bot.GitHubAPI)
        api._session = MagicMock()
        api.timeout_sec = 5
        return api

    def test_errors_array_raises(self):
        api = self._make_api()
        resp = MagicMock()
        resp.json.return_value = {
            "errors": [{"message": "bad query"}, {"message": "rate limited"}],
        }
        resp.raise_for_status = MagicMock()
        api._session.post.return_value = resp
        with self.assertRaises(neo_bot.GraphQLError) as cm:
            api.query("query {}", {})
        self.assertIn("bad query", str(cm.exception))
        self.assertIn("rate limited", str(cm.exception))

    def test_no_errors_returns_body(self):
        api = self._make_api()
        resp = MagicMock()
        resp.json.return_value = {"data": {"x": 1}}
        resp.raise_for_status = MagicMock()
        api._session.post.return_value = resp
        out = api.query("query {}", {})
        self.assertEqual(out, {"data": {"x": 1}})

    def test_partial_errors_with_data_does_not_raise(self):
        """GitHub union queries return both `data` and partial `errors`
        (e.g. when looking up a PR by number, the issue/discussion branches
        return null + an error each)."""
        api = self._make_api()
        resp = MagicMock()
        resp.json.return_value = {
            "data": {
                "repository": {
                    "issue": None,
                    "pullRequest": {"number": 4861, "title": "x"},
                    "discussion": None,
                }
            },
            "errors": [
                {"message": "Could not resolve to an Issue with the number of 4861."},
                {"message": "Could not resolve to a Discussion with the number of 4861."},
            ],
        }
        resp.raise_for_status = MagicMock()
        api._session.post.return_value = resp
        out = api.query("query {}", {})
        self.assertEqual(out["data"]["repository"]["pullRequest"]["number"], 4861)

    def test_pr_lookup_with_partial_errors_returns_pr(self):
        """End-to-end through find_by_id: a PR with sibling-branch errors
        must still return the PR entity (regression test for the crash on
        looking up PR #4861)."""
        api = self._make_api()
        api._session.post.return_value = MagicMock()
        api._session.post.return_value.raise_for_status = MagicMock()
        api._session.post.return_value.json.return_value = {
            "data": {
                "repository": {
                    "issue": None,
                    "pullRequest": {
                        "number": 4861,
                        "title": "fix something",
                        "url": "https://github.com/neomutt/neomutt/pull/4861",
                        "createdAt": "2024-01-01T00:00:00Z",
                        "author": {"login": "alice"},
                    },
                    "discussion": None,
                }
            },
            "errors": [
                {"message": "Could not resolve to an Issue with the number of 4861."},
                {"message": "Could not resolve to a Discussion with the number of 4861."},
            ],
        }
        entity = api.find_by_id(4861, "neomutt", "neomutt")
        self.assertIsInstance(entity, neo_bot.PullRequest)
        self.assertEqual(entity.number, 4861)
        self.assertEqual(entity.user, "alice")


# ---------------------------------------------------------------------------
# Robustness #3: HTTPAdapter retry mounted on the session
# ---------------------------------------------------------------------------
class TestRetryAdapter(unittest.TestCase):
    def test_retry_adapter_mounted(self):
        api = neo_bot.GitHubAPI.__new__(neo_bot.GitHubAPI)
        session = api._init_session("tok")
        adapter = session.get_adapter("https://api.github.com/")
        self.assertIsNotNone(adapter)
        retry = adapter.max_retries
        self.assertEqual(retry.total, 3)
        self.assertIn(502, retry.status_forcelist)
        self.assertIn(429, retry.status_forcelist)


# ---------------------------------------------------------------------------
# Robustness #7: Issue.deleted removed
# ---------------------------------------------------------------------------
class TestIssueDataclass(unittest.TestCase):
    def test_no_deleted_field(self):
        fields = {f.name for f in neo_bot.Issue.__dataclass_fields__.values()}
        self.assertNotIn("deleted", fields)


# ---------------------------------------------------------------------------
# Robustness #11: GraphQL query no longer uses `comments(first: 0)`
# ---------------------------------------------------------------------------
class TestGraphQLQuery(unittest.TestCase):
    def test_no_first_zero(self):
        import queries as q
        self.assertNotIn("first: 0", q.FETCH_ALL_BY_ID)
        self.assertIn("totalCount", q.FETCH_ALL_BY_ID)


# ---------------------------------------------------------------------------
# Robustness #12: graceful shutdown via SIGTERM/SIGINT
# ---------------------------------------------------------------------------
class TestSignalHandlers(unittest.TestCase):
    def test_signal_handler_calls_disconnect_and_exits(self):
        import signal as _signal
        bot = MagicMock()
        # Capture the registered handlers; restore originals after.
        old_term = _signal.getsignal(_signal.SIGTERM)
        old_int = _signal.getsignal(_signal.SIGINT)
        try:
            neo_bot.install_signal_handlers(bot)
            handler = _signal.getsignal(_signal.SIGTERM)
            self.assertNotEqual(handler, old_term)
            with self.assertRaises(SystemExit) as cm:
                handler(_signal.SIGTERM, None)
            self.assertEqual(cm.exception.code, 0)
            bot.disconnect.assert_called_with("shutting down")
        finally:
            _signal.signal(_signal.SIGTERM, old_term)
            _signal.signal(_signal.SIGINT, old_int)


# ---------------------------------------------------------------------------
# Robustness #13: multi-channel + per-channel default user/repo
# ---------------------------------------------------------------------------
class TestChannelSpec(unittest.TestCase):
    def test_bare_name_uses_default(self):
        self.assertEqual(
            neo_bot.parse_channel_spec("foo", "u", "r"),
            ("#foo", "u", "r"),
        )

    def test_name_with_owner_repo(self):
        self.assertEqual(
            neo_bot.parse_channel_spec("#bar:acme/widget", "u", "r"),
            ("#bar", "acme", "widget"),
        )

    def test_invalid_spec_raises(self):
        with self.assertRaises(ValueError):
            neo_bot.parse_channel_spec("foo:nopeslash", "u", "r")

    def test_amp_channel(self):
        self.assertEqual(
            neo_bot.parse_channel_spec("&local", "u", "r"),
            ("&local", "u", "r"),
        )


class TestMultiChannel(TestBotBehavior):
    def _make_multi_bot(self):
        bot = self._make_bot()
        # Reset _channels (TestBotBehavior._make_bot doesn't set it).
        bot._channels = {
            "#neomutt": ("neomutt", "neomutt"),
            "#otherproj": ("acme", "widget"),
        }
        return bot

    def test_join_all_channels_on_welcome(self):
        bot = self._make_multi_bot()
        c = MagicMock()
        e = MagicMock()
        bot.on_welcome(c, e)
        joined = sorted(call.args[0] for call in c.join.call_args_list)
        self.assertEqual(joined, ["#neomutt", "#otherproj"])

    def test_per_channel_defaults_used(self):
        bot = self._make_multi_bot()
        bot.api.find_by_id.return_value = neo_bot.Issue(
            number=42, user="alice", title="t",
            url="https://example.com/42",
            date=datetime.datetime.now(datetime.timezone.utc),
        )
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        e = MagicMock()
        e.arguments = ["#42"]
        e.source.nick = "alice"
        e.target = "#otherproj"
        bot.on_pubmsg(c, e)
        # Bot should have looked up using the otherproj defaults.
        bot.api.find_by_id.assert_called_with("42", "acme", "widget")

    def test_explicit_user_repo_overrides_per_channel(self):
        bot = self._make_multi_bot()
        bot.api.find_by_id.return_value = neo_bot.Issue(
            number=1, user="alice", title="t",
            url="https://example.com/1",
            date=datetime.datetime.now(datetime.timezone.utc),
        )
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        e = MagicMock()
        e.arguments = ["see foo/bar#1"]
        e.source.nick = "alice"
        e.target = "#otherproj"
        bot.on_pubmsg(c, e)
        bot.api.find_by_id.assert_called_with("1", "foo", "bar")

    def test_kick_only_rejoins_us(self):
        bot = self._make_multi_bot()
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        # Someone else got kicked.
        e = MagicMock()
        e.target = "#neomutt"
        e.arguments = ["alice", "bye"]
        bot.on_kick(c, e)
        bot.reactor.scheduler.execute_after.assert_not_called()

    def test_kick_rejoins_kicked_channel(self):
        bot = self._make_multi_bot()
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        e = MagicMock()
        e.target = "#otherproj"
        e.arguments = ["neo-bot", "bye"]
        bot.on_kick(c, e)
        bot.reactor.scheduler.execute_after.assert_called_once()
        # Verify the lambda joins the same channel.
        delay, fn = bot.reactor.scheduler.execute_after.call_args[0]
        fn()
        c.join.assert_called_with("#otherproj")

    def test_kick_unknown_channel_ignored(self):
        bot = self._make_multi_bot()
        c = MagicMock()
        c.get_nickname.return_value = "neo-bot"
        e = MagicMock()
        e.target = "#stranger"
        e.arguments = ["neo-bot", "bye"]
        bot.on_kick(c, e)
        bot.reactor.scheduler.execute_after.assert_not_called()


class TestExtraChannelCLI(unittest.TestCase):
    def test_extra_channel_repeatable(self):
        args = neo_bot.parse_args([
            "s", "neomutt", "n", "-k", "/tmp/tok",
            "--channel", "extra1",
            "--channel", "extra2:owner/repo",
        ])
        self.assertEqual(args.extra_channels, ["extra1", "extra2:owner/repo"])


if __name__ == "__main__":
    unittest.main()
