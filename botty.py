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

class TestBot(irc.bot.SingleServerIRCBot):
    def __init__(self, channel, nickname, server, port=6667):
        irc.bot.SingleServerIRCBot.__init__(self, [(server, port)], nickname, nickname)
        self.channel = channel
        self.issue_re = re.compile(ur'(?:^|\s|pr|issue|(?:(?P<user>[\w\.\-]+)/)?(?P<repo>[\w\.\-]+))?\#(?P<num>[0-9]+)(?=(?:\s|$))', re.I)

    def on_nicknameinuse(self, c, e):
        c.nick(c.get_nickname() + "_")

    def on_welcome(self, c, e):
        c.join(self.channel)

    def on_pubmsg(self, c, e):
        msgs = e.arguments
        for msg in msgs:
            for user, repo, num in self.issue_re.findall(msg):
                self.check_num(c, num, user, repo)
        return

    def check_num(self, c, num, user, repo):
        if user == u'':
            user = u'neomutt'
        if repo == u'':
            repo = u'neomutt'
        url = u'https://github.com/' + user + u'/' + repo + u'/issues/' + num
        req = requests.get(url)
        if req.status_code == 200:
            title = html.fromstring(req.content).xpath('//span[@class="js-issue-title"]/text()')[0].strip()
            val = req.url.split('/')[5]
            if val == u'pull':
                val = u'PR "' + title + u'": '
            elif val == u'issues':
                val = u'Issue "' + title + u'": '
            else:
                val += u' "' + title + u'": '
            c.privmsg(self.channel, val + req.url)

def main():
    import sys
    if len(sys.argv) != 4:
        print("Usage: testbot <server[:port]> <channel> <nickname>")
        sys.exit(1)

    s = sys.argv[1].split(":", 1)
    server = s[0]
    if len(s) == 2:
        try:
            port = int(s[1])
        except ValueError:
            print("Error: Erroneous port.")
            sys.exit(1)
    else:
        port = 6667
    channel = sys.argv[2]
    nickname = sys.argv[3]

    bot = TestBot(channel, nickname, server, port)
    bot.start()

if __name__ == "__main__":
    main()

