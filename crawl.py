#!/usr/bin/env python3

import logging
import re
import signal
import socket
import sys
import urllib.parse

import tulip
from tulip import http_client

END = '\n'
MAXTASKS = 100


class Crawler:

    def __init__(self, rooturl):
        self.rooturl = rooturl
        self.todo = set()
        self.busy = set()
        self.done = {}
        self.tasks = set()
        self.waiter = None
        self.addurl(self.rooturl, '')  # Set initial work.
        self.run()  # Kick off work.

    def addurl(self, url, parenturl):
        url = urllib.parse.urljoin(parenturl, url)
        url, frag = urllib.parse.urldefrag(url)
        if not url.startswith(self.rooturl):
            return False
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
            print(len(complete), 'completed tasks,', len(self.tasks),
                  'still pending   ', end=END)
            for task in complete:
                try:
                    yield from task
                except Exception as exc:
                    print('Exception in task:', exc, end=END)
            while self.todo and len(self.tasks) < MAXTASKS:
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
        p = None
        try:
            print('processing', url, end=END)
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
                          'retrying after sleep', delay, '...', end=END)
                    yield from tulip.sleep(delay)
                    delay *= 2
            if status[:3] in ('301', '302'):
                # Redirect.
                u = headers.get('location') or headers.get('uri')
                if self.addurl(u, url):
                    print('  ', url, status[:3], 'redirect to', u, end=END)
            elif status.startswith('200'):
                ctype = headers.get_content_type()
                if ctype == 'text/html':
                    while True:
                        line = yield from stream.readline()
                        if not line:
                            break
                        line = line.decode('utf-8', 'replace')
                        urls = re.findall(r'(?i)href=["\']?([^\s"\'<>]+)',
                                          line)
                        for u in urls:
                            if self.addurl(u, url):
                                print('  ', url, 'href to', u, end=END)
            ok = True
        finally:
            if p is not None:
                p.transport.close()
            self.done[url] = ok
            self.busy.remove(url)
            if not ok:
                print('failure for', url, sys.exc_info(), end=END)
            waiter = self.waiter
            if waiter is not None:
                self.waiter = None
                waiter.set_result(None)


def main():
    rooturl = sys.argv[1]
    c = Crawler(rooturl)
    loop = tulip.get_event_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, loop.stop)
    except RuntimeError:
        pass
    loop.run_forever()
    print('todo:', len(c.todo))
    print('busy:', len(c.busy))
    print('done:', len(c.done), '; ok:', sum(c.done.values()))
    print('tasks:', len(c.tasks))


if __name__ == '__main__':
    if '--iocp' in sys.argv:
        from tulip import events, windows_events
        sys.argv.remove('--iocp')
        logging.info('using iocp')
        el = windows_events.ProactorEventLoop()
        events.set_event_loop(el)
    main()
