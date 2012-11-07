"""Event loop and related classes.

The event loop can be broken up into a pollster (the part responsible
for telling us when file descriptors are ready) and the event loop
proper, which wraps a pollster with functionality for scheduling
callbacks, immediately or at a given time in the future.

Whenever a public API takes a callback, subsequent positional
arguments will be passed to the callback if/when it is called.  This
avoids the proliferation of trivial lambdas implementing closures.
Keyword arguments for the callback are not supported; this is a
conscious design decision, leaving the door open for keyword arguments
to modify the meaning of the API call itself.

There are several implementations of the pollster part, several using
esoteric system calls that exist only on some platforms.  These are:

- kqueue (most BSD systems)
- epoll (newer Linux systems)
- poll (most UNIX systems)
- select (all UNIX systems, and Windows)
- TODO: Support IOCP on Windows and some UNIX platforms.

NOTE: We don't use select on systems where any of the others is
available, because select performs poorly as the number of file
descriptors goes up.  The ranking is roughly:

  1. kqueue, epoll, IOCP
  2. poll
  3. select

TODO:
- Optimize the various pollsters.
- Unittests.
"""

import collections
import concurrent.futures
import heapq
import logging
import os
import select
import time
import socket

# local imports
from proactor import Proactor, Future


class DelayedCall:
    """Object returned by callback registration methods."""

    def __init__(self, when, callback, args, kwds=None):
        self.when = when
        self.callback = callback
        self.args = args
        self.kwds = kwds
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def __lt__(self, other):
        return self.when < other.when

    def __le__(self, other):
        return self.when <= other.when

    def __eq__(self, other):
        return self.when == other.when


class EventLoop:
    """Event loop functionality.

    This defines public APIs call_soon(), call_later(), run_once() and
    run().  It also wraps Pollster APIs register_reader(),
    register_writer(), remove_reader(), remove_writer() with
    add_reader() etc.

    This class's instance variables are not part of its API.
    """

    def __init__(self, proactor=None):
        super().__init__()
        if proactor is None:
            logging.info('Using proactor: %s', Proactor.__name__)
            proactor = Proactor()
        self.proactor = proactor
        self.ready = collections.deque()  # [(callback, args), ...]
        self.scheduled = []  # [(when, callback, args), ...]

    def add_callback(self, dcall):
        """Add a DelayedCall to ready or scheduled."""
        if dcall.cancelled:
            return
        if dcall.when is None:
            self.ready.append(dcall)
        else:
            heapq.heappush(self.scheduled, dcall)

    def call_soon(self, callback, *args):
        """Arrange for a callback to be called as soon as possible.

        This operates as a FIFO queue, callbacks are called in the
        order in which they are registered.  Each callback will be
        called exactly once.

        Any positional arguments after the callback will be passed to
        the callback when it is called.
        """
        dcall = DelayedCall(None, callback, args)
        self.ready.append(dcall)
        return dcall

    def call_later(self, when, callback, *args):
        """Arrange for a callback to be called at a given time.

        Return an object with a cancel() method that can be used to
        cancel the call.

        The time can be an int or float, expressed in seconds.

        If when is small enough (~11 days), it's assumed to be a
        relative time, meaning the call will be scheduled that many
        seconds in the future; otherwise it's assumed to be a posix
        timestamp as returned by time.time().

        Each callback will be called exactly once.  If two callbacks
        are scheduled for exactly the same time, it undefined which
        will be called first.

        Any positional arguments after the callback will be passed to
        the callback when it is called.
        """
        if when < 10000000:
            when += time.time()
        dcall = DelayedCall(when, callback, args)
        heapq.heappush(self.scheduled, dcall)
        return dcall

    def run_once(self):
        """Run one full iteration of the event loop.

        This calls all currently ready callbacks, polls for I/O,
        schedules the resulting callbacks, and finally schedules
        'call_later' callbacks.
        """
        # TODO: Break each of these into smaller pieces.
        # TODO: Pass in a timeout or deadline or something.
        # TODO: Refactor to separate the callbacks from the readers/writers.
        # TODO: As step 4, run everything scheduled by steps 1-3.
        # TODO: An alternative API would be to do the *minimal* amount
        # of work, e.g. one callback or one I/O poll.

        # This is the only place where callbacks are actually *called*.
        # All other places just add them to ready.
        # TODO: Ensure this loop always finishes, even if some
        # callbacks keeps registering more callbacks.
        while self.ready:
            dcall = self.ready.popleft()
            if not dcall.cancelled:
                try:
                    if dcall.kwds:
                        dcall.callback(*dcall.args, **dcall.kwds)
                    else:
                        dcall.callback(*dcall.args)
                except Exception:
                    logging.exception('Exception in callback %s %r',
                                      dcall.callback, dcall.args)

        # Remove delayed calls that were cancelled from head of queue.
        while self.scheduled and self.scheduled[0].cancelled:
            heapq.heappop(self.scheduled)

        # Inspect the poll queue.
        if self.proactor.pollable():
            if self.scheduled:
                when = self.scheduled[0].when
                timeout = max(0, when - time.time())
            else:
                timeout = None
            t0 = time.time()
            # done callbacks added to ready futures get run by poll()
            self.proactor.poll(timeout)
            t1 = time.time()
            argstr = '' if timeout is None else ' %.3f' % timeout
            if t1-t0 >= 1:
                level = logging.INFO
            else:
                level = logging.DEBUG
            logging.log(level, 'poll%s took %.3f seconds', argstr, t1-t0)

        # Handle 'later' callbacks that are ready.
        while self.scheduled:
            dcall = self.scheduled[0]
            if dcall.when > time.time():
                break
            dcall = heapq.heappop(self.scheduled)
            self.call_soon(dcall.callback, *dcall.args)

    def run(self):
        """Run the event loop until there is no work left to do.

        This keeps going as long as there are either readable and
        writable file descriptors, or scheduled callbacks (of either
        variety).
        """
        while self.ready or self.scheduled or self.proactor.pollable():
            self.run_once()



MAX_WORKERS = 5  # Default max workers when creating an executor.

try:
    from socket import socketpair
except ImportError:
    def socketpair():
        with socket.socket() as l:
            l.bind(('127.0.0.1', 0))
            l.listen(1)
            c = socket.socket()
            c.connect(l.getsockname())
            a, _ = l.accept()
            return a, c

class ThreadRunner:
    """Helper to submit work to a thread pool and wait for it.

    This is the glue between the single-threaded callback-based async
    world and the threaded world.  Use it to call functions that must
    block and don't have an async alternative (e.g. getaddrinfo()).

    The only public API is submit().
    """

    def __init__(self, eventloop, executor=None):
        self.eventloop = eventloop
        self.executor = executor  # Will be constructed lazily.
        self.rsock, self.wsock = socketpair()
        self.rsock.settimeout(0)
        self.active_count = 0
        self.read_future = None

    def start_read(self):
        while self.active_count > 0:
            try:
                res = self.eventloop.proactor.recv(self.rsock, 8192)
            except Future as f:
                self.read_future = f
                self.read_future.add_done_callback(self.read_callback)
                break
            else:
                self.active_count -= len(res)

    def read_callback(self, f):
        assert self.active_count > 0, self.active_count
        assert f is self.read_future
        try:
            self.active_count -= len(self.read_future.result())
        except OSError:
            pass
        finally:
            self.read_future = None
        assert self.active_count >= 0, self.active_count
        if self.active_count > 0:
            self.start_read()

    def submit(self, func, *args, executor=None, insert_callback=None):
        """Submit a function to the thread pool.

        This returns a concurrent.futures.Future instance.  The caller
        should not wait for that, but rather add a callback to it.
        """
        if executor is None:
            executor = self.executor
            if executor is None:
                # Lazily construct a default executor.
                # TODO: Should this be shared between threads?
                executor = concurrent.futures.ThreadPoolExecutor(MAX_WORKERS)
                self.executor = executor
        assert self.active_count >= 0, self.active_count
        self.active_count += 1
        future = executor.submit(func, *args)
        if self.read_future is None or self.read_future.done():
            self.start_read()
        if insert_callback is not None:
            future.add_done_callback(insert_callback)
        def done_callback(future):
            self.wsock.sendall(b'x')
        future.add_done_callback(done_callback)
        return future
