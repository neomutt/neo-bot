#! /usr/bin/env python
#
# Example program using irc.bot.
#
# Joel Rosdahl <joel@rosdahl.net>

"""A simple example bot.

This is an example bot that uses the SingleServerIRCBot class from
irc.bot.  The bot enters a channel and listens for commands in
private messages and channel traffic.  Commands in channel messages
are given by prefixing the text by the bot name followed by a colon.
It also responds to DCC CHAT invitations and echos data sent in such
sessions.

The known commands are:

    stats -- Prints some channel information.

    disconnect -- Disconnect the bot.  The bot will try to reconnect
                  after 60 seconds.

    die -- Let the bot cease to exist.

    dcc -- Let the bot invite you to a DCC CHAT connection.
"""

import irc.bot
import irc.strings
from irc.client import ip_numstr_to_quad, ip_quad_to_numstr
import re
import requests
from lxml import html
import time
from datetime import datetime, timedelta

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
                if msg.startswith(self.nickname):
                    self.check_num(c, answer_to, num, user, repo, True)
                else:
                    self.check_num(c, answer_to, num, user, repo, False)

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

    def check_num(self, c, answer_to, num, user, repo, force):
        if user == "":
            user = self.user
        if repo == "":
            repo = self.repo

        url = "https://github.com/" + user + "/" + repo + "/issues/" + num
        req = requests.get(url)
        if req.status_code == 200:
            content = html.fromstring(req.content)

            date = content.xpath(
                '//h3[@class="timeline-comment-header-text f5'
                ' text-normal"]/a[@class="link-gray js-timestamp"]/relative-time'
            )[0].attrib["datetime"]
            print(date)
            date = datetime.strptime(date, "%Y-%m-%dT%H:%M:%SZ")

            title = content.xpath('//span[@class="js-issue-title"]/text()')[0].strip()
            print(title)

            val = req.url.split("/")[5]
            if val == "pull":
                val = 'PR "' + title + '": '
            elif val == "issues":
                val = 'Issue "' + title + '": '
            else:
                val += ' "' + title + '": '

            if force or date + self.max_age > datetime.now():
                print("SENT: " + val + req.url)
                c.privmsg(answer_to, val + req.url)
            else:
                print("NOT SENT:" + val + req.url)


def main():
    import sys
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
        args.channel,
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
