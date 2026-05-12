"""Tests for the neo-bot fixes (bugs 1-10)."""

import datetime
import importlib
import sys
import time
import types
import unittest
from unittest.mock import MagicMock


def _install_irc_stub():
    """The `irc` package is a runtime-only dependency; stub it for tests."""
    if "irc" in sys.modules:
        return

    irc_pkg = types.ModuleType("irc")

    irc_bot = types.ModuleType("irc.bot")

    class _SingleServerIRCBot:
        def __init__(self, *args, **kwargs):
            self.reactor = MagicMock()

        def start(self):
            pass

    irc_bot.SingleServerIRCBot = _SingleServerIRCBot

    irc_strings = types.ModuleType("irc.strings")

    irc_pkg.bot = irc_bot
    irc_pkg.strings = irc_strings

    sys.modules["irc"] = irc_pkg
    sys.modules["irc.bot"] = irc_bot
    sys.modules["irc.strings"] = irc_strings


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
        return bot

    def test_privmsg_replies_to_source_not_channel(self):
        """Bug #6: on_privmsg used to leak to self.channel."""
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
        self.assertEqual(target, "alice")
        self.assertNotEqual(target, "#neomutt")

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
        e = MagicMock()
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


if __name__ == "__main__":
    unittest.main()
