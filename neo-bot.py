#! /usr/bin/env python
# Joel Rosdahl <joel@rosdahl.net>

import irc.bot
import irc.strings
import re
import requests
import time
from datetime import datetime, timedelta
from dataclasses import dataclass


@dataclass
class Issue:
    date: datetime
    type: str
    user: str
    title: str
    url: str
    deleted: bool


class TestBot(irc.bot.SingleServerIRCBot):
    def __init__(self, channel, nickname, server, port, user, repo, max_age):
        super().__init__(((server, port),), nickname, nickname)
        self.channel = channel
        self.issue_re = re.compile(
            r"(?:^|\s|pr|issue|(?:(?P<user>[\w\.\-]+)/)?(?P<repo>[\w\.\-]+))?\#(?P<num>[0-9]+)\b",
            re.I,
        )
        self.user = user
        self.repo = repo
        self.max_age = timedelta(days=max_age)
        self.nickname = nickname

    def on_nicknameinuse(self, c, e):
        c.nick(c.get_nickname() + "_")
        self.nickname = c.get_nickname()

    def on_welcome(self, c, e):
        c.join(self.channel)

    def on_privmsg(self, c, e):
        return self._process_message(c, e.source.nick, e)

    def on_pubmsg(self, c, e):
        return self._process_message(c, self.channel, e)

    def _process_message(self, c, answer_to, e):
        msgs = e.arguments
        for msg in msgs:
            for user, repo, num in self.issue_re.findall(msg):
                issue = self.check_num(num, user, repo)
                if issue is None:
                    print(f"Issue {num} not found")
                    continue

                is_private = msg.startswith(self.nickname)

                if issue.deleted:
                    c.privmsg(answer_to, f"The issue {num} has been deleted")
                    print(f"The issue {num} has been deleted")
                    continue

                is_old = (issue.date + self.max_age) <= datetime.now()
                reply = f'{issue.type} by @{issue.user} "{issue.title}": {issue.url}'
                if not is_old or (is_old and is_private):
                    c.privmsg(answer_to, reply)
                    print("SENT: " + reply)
                else:
                    print("NOT SENT: " + reply)

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

    def check_num(self, num, user=None, repo=None):
        if not user:
            user = self.user
        if not repo:
            repo = self.repo

        req = requests.get(f"https://api.github.com/repos/{user}/{repo}/issues/{num}")
        if req.status_code == 410:
            return Issue(deleted=True)

        elif req.status_code == 200:
            issue = req.json()
            date = datetime.strptime(issue["updated_at"], "%Y-%m-%dT%H:%M:%SZ")
            title = issue["title"]
            url = issue["html_url"]
            user = issue["user"]["login"]
            type = "PR" if "pull_request" in issue else "Issue"
            return Issue(
                date=date, type=type, user=user, title=title, url=url, deleted=False
            )

        else:
            return None


def main():
    import argparse

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

    args = parser.parse_args()
    print(args)

    bot = TestBot(
        f"#{args.channel}",
        args.nickname,
        args.server,
        args.port,
        args.user,
        args.repo,
        args.max_age,
    )
    bot.start()


if __name__ == "__main__":
    main()
