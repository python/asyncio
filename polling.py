"""I/O loop based on poll().

TODO:
- Docstrings.
- Use _ for non-public methods and instance variables.
- Support some of the other POLL* flags.
- Use kpoll(), epoll() etc. in preference.
- Fall back on select() if no poll() variant at all.
- Keyword args to callbacks.
"""

import collections
import concurrent.futures
import heapq
import logging
import os
import select
import time


class Pollster:

    def __init__(self):
        self.ready = collections.deque()  # [(callback, args), ...]
        self.scheduled = []  # [(when, callback, args), ...]
        self.readers = {}  # {fd: (callback, args), ...}.
        self.writers = {}  # {fd: (callback, args), ...}.
        self.pollster = select.poll()

    def update(self, fd):
        assert isinstance(fd, int), fd
        flags = 0
        if fd in self.readers:
            flags |= select.POLLIN
        if fd in self.writers:
            flags |= select.POLLOUT
        if flags:
            self.pollster.register(fd, flags)
        else:
            self.pollster.unregister(fd)

    def add_reader(self, fd, callback, *args):
        self.readers[fd] = (callback, args)
        self.update(fd)

    def add_writer(self, fd, callback, *args):
        self.writers[fd] = (callback, args)
        self.update(fd)

    def remove_reader(self, fd):
        del self.readers[fd]
        self.update(fd)

    def remove_writer(self, fd):
        del self.writers[fd]
        self.update(fd)

    def call_soon(self, callback, *args):
        self.ready.append((callback, args))

    def call_later(self, when, callback, *args):
        if when < 10000000:
            when += time.time()
        heapq.heappush(self.scheduled, (when, callback, args))

    def rawpoll(self, timeout=None):
        # Timeout is in seconds, but poll() takes milliseconds. :-(
        msecs = None if timeout is None else int(1000 * timeout)
        quads = []
        for fd, flags in self.pollster.poll(msecs):
            if flags & (select.POLLIN | select.POLLHUP):
                if fd in self.readers:
                    callback, args = self.readers[fd]
                    quads.append((fd, flags, callback, args))
            if flags & (select.POLLOUT | select.POLLHUP):
                if fd in self.writers:
                    callback, args = self.writers[fd]
                    quads.append((fd, flags, callback, args))
        return quads

    def run_once(self):
        # TODO: Break each of these into smaller pieces.
        # TODO: Pass in a timeout or deadline or something.
        # TODO: Refactor to separate the callbacks from the readers/writers.

        # This is the only place where callbacks are actually *called*.
        # All other places just add them to ready.
        while self.ready:
            callback, args = self.ready.popleft()
            try:
                callback(*args)
            except Exception:
                logging.exception('Exception in callback %s %r', callback, args)

        # Inspect the poll queue.
        if self.readers or self.writers:
            if self.scheduled:
                when, _, _ = self.scheduled[0]
                timeout = max(0, when - time.time())
            else:
                timeout = None
            quads = self.rawpoll(timeout)
            for fd, flag, callback, args in quads:
                self.call_soon(callback, *args)

        # Handle 'later' callbacks that are ready.
        while self.scheduled:
            when, _, _ = self.scheduled[0]
            if when > time.time():
                break
            when, callback, args = heapq.heappop(self.scheduled)
            self.call_soon(callback, *args)

    def run(self):
        while self.ready or self.readers or self.writers or self.scheduled:
            self.run_once()


class ThreadRunner:

    def __init__(self, ioloop, max_workers=5):
        self.ioloop = ioloop
        self.threadpool = concurrent.futures.ThreadPoolExecutor(max_workers)
        self.pipe_read_fd, self.pipe_write_fd = os.pipe()
        self.active_count = 0

    def read_callback(self):
        # Semi-permanent callback while at least one future is active.
        assert self.active_count > 0, self.active_count
        data = os.read(self.pipe_read_fd, 8192)
        self.active_count -= len(data)
        if self.active_count == 0:
            self.ioloop.remove_reader(self.pipe_read_fd)
        assert self.active_count >= 0, self.active_count

    def submit(self, func, *args, **kwds):
        assert self.active_count >= 0, self.active_count
        future = self.threadpool.submit(func, *args, **kwds)
        if self.active_count == 0:
            self.ioloop.add_reader(self.pipe_read_fd, self.read_callback)
        self.active_count += 1
        def done_callback(future):
            os.write(self.pipe_write_fd, b'x')
        future.add_done_callback(done_callback)
        return future
