#!/usr/bin/env python3

import logging
import re
import signal
import socket
import sys
import urllib.parse

import tulip
from tulip import http_client


class Crawler:

    def __init__(self, rooturl):
        self.rooturl = rooturl
        self.todo = set()
        self.busy = set()
        self.done = {}
        self.tasks = set()
        self.waiter = None
        self.add(self.rooturl)  # Set initial work.
        self.run()  # Kick off work.

    def add(self, url):
        if url in self.busy or url in self.done or url in self.todo:
            return False
        self.todo.add(url)
        waiter = self.waiter
        if waiter is not None:
            self.waiter = None
            waiter.set_result(None)
        return True

    @tulip.task
    def run(self):
        while self.todo or self.busy or self.tasks:
            complete, self.tasks = yield from tulip.wait(self.tasks, timeout=0)
            for task in complete:
                try:
                    yield from task
                except Exception as exc:
                    logging.warn('Exception in task: %s', exc)
            while self.todo:
                url = self.todo.pop()
                self.busy.add(url)
                self.tasks.add(self.process(url))  # Async task.
            if self.busy:
                self.waiter = tulip.Future()
                yield from self.waiter
        tulip.get_event_loop().stop()

    @tulip.task
    def process(self, url):
        ok = False
        try:
            print('processing', url)
            scheme, netloc, path, query, fragment = urllib.parse.urlsplit(url)
            if not path:
                path = '/'
            if query:
                path = '?'.join([path, query])
            p = http_client.HttpClientProtocol(netloc, path=path,
                                               ssl=(scheme=='https'))
            delay = 1
            while True:
                try:
                    status, headers, stream = yield from p.connect()
                    break
                except socket.error as exc:
                    if delay >= 60:
                        raise
                    print('...', url, 'has error', repr(str(exc)),
                          'retrying after sleep', delay, '...')
                    yield from tulip.sleep(delay)
                    delay *= 2
            if status.startswith('200'):
                ctype = headers.get_content_type()
                if ctype == 'text/html':
                    while True:
                        line = yield from stream.readline()
                        if not line:
                            break
                        line = line.decode('utf-8')
                        urls = re.findall(r'(?i)href=["\']?([^\s"\'<>]+)',
                                          line)
                        for u in urls:
                            u, frag = urllib.parse.urldefrag(u)
                            u = urllib.parse.urljoin(self.rooturl, u)
                            if u.startswith(self.rooturl):
                                if self.add(u):
                                    print(' ', url, '->', u)
            ok = True
        finally:
            self.done[url] = ok
            self.busy.remove(url)
            if not ok:
                print('failure for', url, sys.exc_info())
            waiter = self.waiter
            if waiter is not None:
                self.waiter = None
                waiter.set_result(None)


def main():
    rooturl = sys.argv[1]
    c = Crawler(rooturl)
    loop = tulip.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, loop.stop)
    loop.run_forever()
    print('todo:', len(c.todo))
    print('busy:', len(c.busy))
    print('done:', len(c.done), '; ok:', sum(c.done.values()))
    print('tasks:', len(c.tasks))


if __name__ == '__main__':
    main()
