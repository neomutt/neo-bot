#! /usr/bin/env python
# Joel Rosdahl <joel@rosdahl.net>

import argparse
import re
import time
from datetime import datetime, timedelta
from dataclasses import dataclass

import irc.bot
import irc.strings
import requests

import queries
from maxageset import MaxAgeSet


class GitHubBot(irc.bot.SingleServerIRCBot):
    def __init__(
        self, api, channel, nickname, server, port, user, repo, max_age, cooldown_min
    ):
        super().__init__(((server, port),), nickname, nickname)
        self.api = api
        self.channel = channel
        self.issue_re = re.compile(
            r"""
            (?:^|\s) # boundary must be space or start of line
            (?:
                (?:(?P<user>[\w\.\-]+)/)? # Optional user, if present must end in slash
                (?P<repo>[\w\.\-]+) # repo
            )? # optional, sane defaults are chosen if not provided
            \#(?P<num>[0-9]+) # issue number
            \b # followed by some word boundary (punctuation, space...)
        """,
            re.IGNORECASE | re.VERBOSE,
        )
        self.user = user
        self.repo = repo
        self.nickname = nickname
        self.max_age = timedelta(days=max_age)
        self.policies = [
            self.reject_if_too_old(),
            self.reject_if_repeated(timedelta(minutes=cooldown_min)),
        ]

    def on_nicknameinuse(self, c, e):
        c.nick(c.get_nickname() + "_")
        self.nickname = c.get_nickname()

    def on_welcome(self, c, e):
        c.join(self.channel)

    def on_privmsg(self, c, e):
        return self._process_message(c, e.source.nick, e)

    def on_action(self, c, e):
        if e.target == c.get_nickname():
            respond_to = e.source.nick  # query, so respond there
        else:
            respond_to = e.target  # channel
        return self._process_message(c, respond_to, e)

    def on_pubmsg(self, c, e):
        return self._process_message(c, self.channel, e)

    def _apply_report_policies(self, msg, entity):
        """Returns None if the bot should reply and an error message otherwise"""
        for f in self.policies:
            resp = f(msg, entity)
            if resp is not None:
                return resp
        return None

    def _process_message(self, c, answer_to, e):
        msgs = e.arguments
        for msg in msgs:
            for user, repo, num in self.issue_re.findall(msg):
                try:
                    entity = self.find_entity_from_id(num, user, repo)
                except Exception as err:
                    print(f"FAILURE: {err}")
                    continue
                if entity is None:
                    print(f"Entity {num} not found")
                    continue

                reject_reason = self._apply_report_policies(msg, entity)
                if reject_reason is not None:
                    print(reject_reason)
                    continue

                reply = entity.render()
                c.privmsg(answer_to, reply)
                print("SENT: " + reply)

    def on_kick(self, c, e):
        print("Parted by ")
        print(e)
        delay = 10
        time.sleep(delay)
        while True:
            try:
                print("... rejoining")
                c.join(self.channel)
                break
            except:
                delay = 2 * delay
                if delay > 300:
                    delay = 300
                time.sleep(delay)

    def on_disconnect(self, c, e):
        print("Disconnected by ")
        print(e)
        delay = 10
        time.sleep(delay)
        while True:
            try:
                print("... reconnecting")
                c.reconnect()
                break
            except:
                delay = 2 * delay
                if delay > 300:
                    delay = 300
                time.sleep(delay)

    def find_entity_from_id(self, num, user=None, repo=None):
        if not user:
            user = self.user
        if not repo:
            repo = self.repo
        return self.api.find_by_id(num, user, repo)

    def reject_if_too_old(self):
        def validate(msg, entity):
            is_mention = msg.startswith(self.nickname)
            is_old = (entity.date + self.max_age) <= datetime.now()
            # if the bot is explicitly mentioned rather than just triggered passively
            # ignore the max age option
            if is_old and not is_mention:
                return f"{entity.number} is too old"
            return None

        return validate

    def reject_if_repeated(self, period: timedelta):
        """rejects if multiple lookups are performed for the same number within period"""
        processed = MaxAgeSet(period)

        def validate(msg, entity):
            if entity.number in processed:
                return f"{entity.number} in cooldown period"
            processed.add(entity.number)
            return None

        return validate


def parse_args():
    parser = argparse.ArgumentParser()

    parser.add_argument("server", help="IRC server to connect to")
    parser.add_argument("channel", help="IRC channel to join")
    parser.add_argument("nickname", help="nickname to use")

    parser.add_argument(
        "-p", "--port", help="port of the IRC server", type=int, default=6667
    )
    parser.add_argument("-u", "--user", help="default github user", default="neomutt")
    parser.add_argument(
        "-r", "--repo", help="default github repository", default="neomutt"
    )
    parser.add_argument(
        "-m",
        "--max_age",
        help="only show issues less than MAX_AGE days old",
        type=int,
        default=365,
    )

    parser.add_argument(
        "-k",
        "--api_token_path",
        help="Path to file containing GH api key",
        required=True,
    )
    parser.add_argument(
        "--cooldown_min",
        help="do not repeat lookups within the given number of minutes",
        type=int,
        default=5,
    )
    return parser.parse_args()


def main():
    args = parse_args()
    print(args)

    api = GitHubAPI(args.api_token_path)
    bot = GitHubBot(
        api,
        f"#{args.channel}",
        args.nickname,
        args.server,
        args.port,
        args.user,
        args.repo,
        args.max_age,
        args.cooldown_min,
    )
    bot.start()


@dataclass
class Issue:
    number: int
    user: str
    title: str
    url: str
    date: datetime
    deleted: bool = False

    def render(self):
        return f'Issue by @{self.user} "{self.title}": {self.url}'


@dataclass
class PullRequest:
    number: int
    title: str
    url: str
    user: str
    date: datetime

    def render(self):
        return f'PR by @{self.user} "{self.title}": {self.url}'


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
        if self.num_comments == 1:
            comment_str = "comment"
        else:
            comment_str = "comments"

        return (
            f'{self.category} discussion by @{self.user} "{self.title}" '
            f"with {self.num_comments} {comment_str}: {self.url}"
        )


class GitHubAPI:
    _GRAPHQL_ENDPOINT = "https://api.github.com/graphql"

    def __init__(self, api_token_path, timeout_sec=5):
        """Grahpql API of GitHub
        Params:
            api_token_path: Path to file containing GH api key.
                            Needs at least the public_repo scope
            timeout_sec: Default request timeout in seconds
        """
        api_key = self._load_api_key(api_token_path)
        self._session = self._init_session(api_key)
        self.timeout_sec = timeout_sec

    def _load_api_key(self, api_token_path):
        with open(api_token_path, "r") as file:
            return file.readline().strip()

    def _init_session(self, api_key):
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {api_key}"})

    def query(self, query, variables=None):
        resp = self.session.post(
            self._GRAPHQL_ENDPOINT,
            json={"query": query, "variables": variables},
            timeout=self.timeout_sec,
        )
        resp.raise_for_status()
        return resp.json()

    def find_by_id(self, id_, user="neomutt", repo="neomutt"):
        vars = {"num": int(id_), "user": user, "repo": repo}
        res = self.query(queries.FETCH_ALL_BY_ID, vars)
        data = res["data"]["repository"]
        if issue := data["issue"]:
            return Issue(
                number=issue["number"],
                user=issue["author"]["login"],
                title=issue["title"],
                url=issue["url"],
                date=format_time(issue["createdAt"]),
            )
        elif pr := data["pullRequest"]:
            return PullRequest(
                number=pr["number"],
                user=pr["author"]["login"],
                title=pr["title"],
                url=pr["url"],
                date=format_time(pr["createdAt"]),
            )
        elif discussion := data["discussion"]:
            return Discussion(
                number=discussion["number"],
                user=discussion["author"]["login"],
                title=discussion["title"],
                url=discussion["url"],
                date=format_time(discussion["createdAt"]),
                num_comments=discussion["comments"]["totalCount"],
                category=self._emoji_from_emojiHTML(
                    discussion["category"]["emojiHTML"]
                ),
            )
        else:
            # not found or someone forgot to update this function after modifying the
            # graphql query
            return

    def _emoji_from_emojiHTML(self, emojiHTML):
        # example category output of the graphql output:
        #  '<div><g-emoji '
        #  'class="g-emoji" '
        #  'alias="wrench" '
        #  'fallback-src="https://github.githubassets.com/images/icons/emoji/unicode/1f527.png">🔧</g-emoji></div>'},
        end = emojiHTML.rindex("</g-emoji>")  # the tag we actually wanna have
        start = emojiHTML.rindex(">", 0, end) + 1  # we only want the inner text
        return emojiHTML[start:end]


def format_time(time):
    return datetime.strptime(time, "%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    main()
