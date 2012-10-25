"""Event loop and related classes.

The event loop can be broken up into a pollster (the part responsible
for telling us when file descriptors are ready) and the event loop
proper, which adds functionality for scheduling callbacks, immediately
or at a given time in the future.

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
- Optimize the various pollster.
- Unittests.
"""

import collections
import concurrent.futures
import heapq
import logging
import os
import select
import time


class PollsterBase:
    """Base class for all polling implementations.

    This defines an interface to register and unregister readers and
    writers (defined as a callback plus optional positional arguments)
    for specific file descriptors, and an interface to get a list of
    events.  There's also an interface to check whether any readers or
    writers are currently registered.  The readers and writers
    attributes are public -- they are simply mappings of file
    descriptors to tuples of (callback, args).
    """

    def __init__(self):
        super().__init__()
        self.readers = {}  # {fd: (callback, args), ...}.
        self.writers = {}  # {fd: (callback, args), ...}.

    def pollable(self):
        """Return True if any readers or writers are currently registered."""
        return bool(self.readers or self.writers)

    # Subclasses are expected to extend the add/remove methods.

    def add_reader(self, fd, callback, *args):
        """Add or update a reader for a file descriptor."""
        self.readers[fd] = (callback, args)

    def add_writer(self, fd, callback, *args):
        """Add or update a writer for a file descriptor."""
        self.writers[fd] = (callback, args)

    def remove_reader(self, fd):
        """Remove the reader for a file descriptor."""
        del self.readers[fd]

    def remove_writer(self, fd):
        """Remove the writer for a file descriptor."""
        del self.writers[fd]

    def poll(self, timeout=None):
        """Poll for events.  A subclass must implement this.

        If timeout is omitted or None, this blocks until at least one
        event is ready.  Otherwise, timeout gives a maximum time to
        wait (an int of float in seconds) -- the method returns as
        soon as at least one event is ready or when the timeout is
        expired.  For a non-blocking poll, pass 0.

        The return value is a list of events; it is empty when the
        timeout expired before any events were ready.  Each event
        is a tuple of the form (fd, flag, callback, args):
          fd: the file descriptor
          flag: 'r' or 'w' (to distinguish readers from writers)
          callback: callback function
          args: arguments tuple for callback
        """
        raise NotImplementedError


class SelectMixin(PollsterBase):
    """Pollster implementation using select."""

    def poll(self, timeout=None):
        readable, writable, _ = select.select(self.readers, self.writers,
                                              [], timeout)
        events = []
        events += ((fd, 'r') + self.readers[fd] for fd in readable)
        events += ((fd, 'w') + self.writers[fd] for fd in writable)
        return events


class PollMixin(PollsterBase):
    """Pollster implementation using poll."""

    def __init__(self):
        super().__init__()
        self._poll = select.poll()

    def _update(self, fd):
        assert isinstance(fd, int), fd
        flags = 0
        if fd in self.readers:
            flags |= select.POLLIN
        if fd in self.writers:
            flags |= select.POLLOUT
        if flags:
            self._poll.register(fd, flags)
        else:
            self._poll.unregister(fd)

    def add_reader(self, fd, callback, *args):
        super().add_reader(fd, callback, *args)
        self._update(fd)

    def add_writer(self, fd, callback, *args):
        super().add_writer(fd, callback, *args)
        self._update(fd)

    def remove_reader(self, fd):
        super().remove_reader(fd)
        self._update(fd)

    def remove_writer(self, fd):
        super().remove_writer(fd)
        self._update(fd)

    def poll(self, timeout=None):
        # Timeout is in seconds, but poll() takes milliseconds.
        msecs = None if timeout is None else int(round(1000 * timeout))
        events = []
        for fd, flags in self._poll.poll(msecs):
            if flags & (select.POLLIN | select.POLLHUP):
                if fd in self.readers:
                    callback, args = self.readers[fd]
                    events.append((fd, 'r', callback, args))
            if flags & (select.POLLOUT | select.POLLHUP):
                if fd in self.writers:
                    callback, args = self.writers[fd]
                    events.append((fd, 'w', callback, args))
        return events


class EPollMixin(PollsterBase):
    """Pollster implementation using epoll."""

    def __init__(self):
        super().__init__()
        self._epoll = select.epoll()

    def _update(self, fd):
        assert isinstance(fd, int), fd
        eventmask = 0
        if fd in self.readers:
            eventmask |= select.EPOLLIN
        if fd in self.writers:
            eventmask |= select.EPOLLOUT
        if eventmask:
            try:
                self._epoll.register(fd, eventmask)
            except IOError:
                self._epoll.modify(fd, eventmask)
        else:
            self._epoll.unregister(fd)

    def add_reader(self, fd, callback, *args):
        super().add_reader(fd, callback, *args)
        self._update(fd)

    def add_writer(self, fd, callback, *args):
        super().add_writer(fd, callback, *args)
        self._update(fd)

    def remove_reader(self, fd):
        super().remove_reader(fd)
        self._update(fd)

    def remove_writer(self, fd):
        super().remove_writer(fd)
        self._update(fd)

    def poll(self, timeout=None):
        if timeout is None:
            timeout = -1  # epoll.poll() uses -1 to mean "wait forever".
        events = []
        for fd, eventmask in self._epoll.poll(timeout):
            if eventmask & select.EPOLLIN:
                if fd in self.readers:
                    callback, args = self.readers[fd]
                    events.append((fd, 'r', callback, args))
            if eventmask & select.EPOLLOUT:
                if fd in self.writers:
                    callback, args = self.writers[fd]
                    events.append((fd, 'w', callback, args))
        return events


class KqueueMixin(PollsterBase):
    """Pollster implementation using kqueue."""

    def __init__(self):
        super().__init__()
        self._kqueue = select.kqueue()

    def add_reader(self, fd, callback, *args):
        if fd not in self.readers:
            kev = select.kevent(fd, select.KQ_FILTER_READ, select.KQ_EV_ADD)
            self._kqueue.control([kev], 0, 0)
        super().add_reader(fd, callback, *args)

    def add_writer(self, fd, callback, *args):
        if fd not in self.readers:
            kev = select.kevent(fd, select.KQ_FILTER_WRITE, select.KQ_EV_ADD)
            self._kqueue.control([kev], 0, 0)
        super().add_writer(fd, callback, *args)

    def remove_reader(self, fd):
        super().remove_reader(fd)
        kev = select.kevent(fd, select.KQ_FILTER_READ, select.KQ_EV_DELETE)
        self._kqueue.control([kev], 0, 0)

    def remove_writer(self, fd):
        super().remove_writer(fd)
        kev = select.kevent(fd, select.KQ_FILTER_WRITE, select.KQ_EV_DELETE)
        self._kqueue.control([kev], 0, 0)

    def poll(self, timeout=None):
        events = []
        max_ev = len(self.readers) + len(self.writers)
        for kev in self._kqueue.control(None, max_ev, timeout):
            fd = kev.ident
            flag = kev.filter
            if flag == select.KQ_FILTER_READ and fd in self.readers:
                callback, args = self.readers[fd]
                events.append((fd, 'r', callback, args))
            elif flag == select.KQ_FILTER_WRITE and fd in self.writers:
                callback, args = self.writers[fd]
                events.append((fd, 'w', callback, args))
        return events


class DelayedCall:
    """Object returned by call_later(); can be used to cancel the call."""

    def __init__(self, when, callback, args):
        self.when = when
        self.callback = callback
        self.args = args
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def __lt__(self, other):
        return self.when < other.when

    def __le__(self, other):
        return self.when <= other.when

    def __eq__(self, other):
        return self.when == other.when


class EventLoopMixin(PollsterBase):
    """Event loop functionality.

    This is an abstract class, inheriting from the abstract class
    PollsterBase.  A concrete class can be formed trivially by
    inheriting from any of the pollster mixin classes; the concrete
    class EventLoop is such a concrete class using the preferred mixin
    given the platform.

    This defines public APIs call_soon(), call_later(), run_once() and
    run().  It also inherits public APIs add_reader(), add_writer(),
    remove_reader(), remove_writer() from the mixin class.  The APIs
    pollable() and poll(), implemented by the mix-in, are not part of
    the public API.

    This class's instance variables are not part of its API.
    """

    def __init__(self):
        super().__init__()
        self.ready = collections.deque()  # [(callback, args), ...]
        self.scheduled = []  # [(when, callback, args), ...]

    def call_soon(self, callback, *args):
        """Arrange for a callback to be called as soon as possible.

        This operates as a FIFO queue, callbacks are called in the
        order in which they are registered.  Each callback will be
        called exactly once.

        Any positional arguments after the callback will be passed to
        the callback when it is called.
        """
        self.ready.append((callback, args))

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
            callback, args = self.ready.popleft()
            try:
                callback(*args)
            except Exception:
                logging.exception('Exception in callback %s %r',
                                  callback, args)

        # Remove delayed calls that were cancelled from head of queue.
        while self.scheduled and self.scheduled[0].cancelled:
            heapq.heappop(self.scheduled)

        # Inspect the poll queue.
        if self.pollable():
            if self.scheduled:
                when = self.scheduled[0].when
                timeout = max(0, when - time.time())
            else:
                timeout = None
            t0 = time.time()
            events = self.poll(timeout)
            t1 = time.time()
            argstr = '' if timeout is None else ' %.3f' % timeout
            logging.debug('poll%s took %.3f seconds', argstr, t1-t0)
            for fd, flag, callback, args in events:
                self.call_soon(callback, *args)

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
        while self.ready or self.scheduled or self.pollable():
            self.run_once()


# Select the most appropriate base class for the platform.
if hasattr(select, 'kqueue'):
    poll_base = KqueueMixin
elif hasattr(select, 'epoll'):
    poll_base = EPollMixin
elif hasattr(select, 'poll'):
    poll_base = PollMixin
else:
    poll_base = SelectMixin

logging.info('Using Pollster base class %r', poll_base.__name__)


class EventLoop(EventLoopMixin, poll_base):
    """Event loop implementation using the optimal pollster mixin."""


class ThreadRunner:
    """Helper to submit work to a thread pool and wait for it.

    This is the glue between the single-threaded callback-based async
    world and the threaded world.  Use it to call functions that must
    block and don't have an async alternative (e.g. getaddrinfo()).

    The only public API is submit().
    """

    def __init__(self, eventloop, max_workers=5):
        self.eventloop = eventloop
        self.threadpool = concurrent.futures.ThreadPoolExecutor(max_workers)
        self.pipe_read_fd, self.pipe_write_fd = os.pipe()
        self.active_count = 0

    def read_callback(self):
        """Semi-permanent callback while at least one future is active."""
        assert self.active_count > 0, self.active_count
        data = os.read(self.pipe_read_fd, 8192)  # Traditional buffer size.
        self.active_count -= len(data)
        if self.active_count == 0:
            self.eventloop.remove_reader(self.pipe_read_fd)
        assert self.active_count >= 0, self.active_count

    def submit(self, func, *args):
        """Submit a function to the thread pool.

        This returns a concurrent.futures.Future instance.  The caller
        should not wait for that, but rather add a callback to it.
        """
        assert self.active_count >= 0, self.active_count
        future = self.threadpool.submit(func, *args)
        if self.active_count == 0:
            self.eventloop.add_reader(self.pipe_read_fd, self.read_callback)
        self.active_count += 1
        def done_callback(future):
            os.write(self.pipe_write_fd, b'x')
        future.add_done_callback(done_callback)
        return future
