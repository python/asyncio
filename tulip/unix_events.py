"""UNIX event loop and related classes.

NOTE: The Pollster classes are not part of the published API.

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

NOTE: We don't use select on systems where any of the others is
available, because select performs poorly as the number of file
descriptors goes up.  The ranking is roughly:

  1. kqueue, epoll, IOCP (best for each platform)
  2. poll (linear in number of file descriptors polled)
  3. select (linear in max number of file descriptors supported)
"""

import collections
import concurrent.futures
import errno
import heapq
import logging
import select
import socket
import sys
import threading
import time

from . import events
from . import futures

try:
    from socket import socketpair
except ImportError:
    assert sys.platform == 'win32'
    from .winsocketpair import socketpair

# Errno values indicating the connection was disconnected.
_DISCONNECTED = frozenset((errno.ECONNRESET,
                           errno.ENOTCONN,
                           errno.ESHUTDOWN,
                           errno.ECONNABORTED,
                           errno.EPIPE,
                           errno.EBADF,
                           ))

# Errno values indicating the socket isn't ready for I/O just yet.
_TRYAGAIN = frozenset((errno.EAGAIN, errno.EWOULDBLOCK, errno.EINPROGRESS))
if sys.platform == 'win32':
    _TRYAGAIN = frozenset(list(_TRYAGAIN) + [errno.WSAEWOULDBLOCK])

# Argument for default thread pool executor creation.
_MAX_WORKERS = 5


class PollsterBase:
    """Base class for all polling implementations.

    This defines an interface to register and unregister readers and
    writers for specific file descriptors, and an interface to get a
    list of events.  There's also an interface to check whether any
    readers or writers are currently registered.
    """

    def __init__(self):
        super().__init__()
        self.readers = {}  # {fd: handler, ...}.
        self.writers = {}  # {fd: handler, ...}.

    def pollable(self):
        """Return the number readers and writers currently registered."""
        # The event loop needs the number since it must subtract one for
        # the self-pipe.
        return len(self.readers) + len(self.writers)

    # Subclasses are expected to extend the add/remove methods.

    def register_reader(self, fd, handler):
        """Add or update a reader for a file descriptor."""
        self.readers[fd] = handler

    def register_writer(self, fd, handler):
        """Add or update a writer for a file descriptor."""
        self.writers[fd] = handler

    def unregister_reader(self, fd):
        """Remove the reader for a file descriptor."""
        del self.readers[fd]

    def unregister_writer(self, fd):
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
        is a handler previously passed to register_reader/writer().
        """
        raise NotImplementedError


if sys.platform != 'win32':

    class SelectPollster(PollsterBase):
        """Pollster implementation using select."""

        def poll(self, timeout=None):
            readable, writable, _ = select.select(self.readers, self.writers,
                                                  [], timeout)
            events = []
            events += (self.readers[fd] for fd in readable)
            events += (self.writers[fd] for fd in writable)
            return events

else:

    class SelectPollster(PollsterBase):
        """Pollster implementation using select."""

        def poll(self, timeout=None):
            # Failed connections are reported as exceptional but not writable.
            readable, writable, exceptional = select.select(
                self.readers, self.writers, self.writers, timeout)
            writable = set(writable).union(exceptional)
            events = []
            events += (self.readers[fd] for fd in readable)
            events += (self.writers[fd] for fd in writable)
            return events


class PollPollster(PollsterBase):
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

    def register_reader(self, fd, handler):
        super().register_reader(fd, handler)
        self._update(fd)

    def register_writer(self, fd, handler):
        super().register_writer(fd, handler)
        self._update(fd)

    def unregister_reader(self, fd):
        super().unregister_reader(fd)
        self._update(fd)

    def unregister_writer(self, fd):
        super().unregister_writer(fd)
        self._update(fd)

    def poll(self, timeout=None):
        # Timeout is in seconds, but poll() takes milliseconds.
        msecs = None if timeout is None else int(round(1000 * timeout))
        events = []
        for fd, flags in self._poll.poll(msecs):
            if flags & ~select.POLLOUT:
                if fd in self.readers:
                    events.append(self.readers[fd])
            if flags & ~select.POLLIN:
                if fd in self.writers:
                    events.append(self.writers[fd])
        return events


class EPollPollster(PollsterBase):
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

    def register_reader(self, fd, handler):
        super().register_reader(fd, handler)
        self._update(fd)

    def register_writer(self, fd, handler):
        super().register_writer(fd, handler)
        self._update(fd)

    def unregister_reader(self, fd):
        super().unregister_reader(fd)
        self._update(fd)

    def unregister_writer(self, fd):
        super().unregister_writer(fd)
        self._update(fd)

    def poll(self, timeout=None):
        if timeout is None:
            timeout = -1  # epoll.poll() uses -1 to mean "wait forever".
        events = []
        for fd, eventmask in self._epoll.poll(timeout):
            if eventmask & ~select.EPOLLOUT:
                if fd in self.readers:
                    events.append(self.readers[fd])
            if eventmask & ~select.EPOLLIN:
                if fd in self.writers:
                    events.append(self.writers[fd])
        return events


class KqueuePollster(PollsterBase):
    """Pollster implementation using kqueue."""

    def __init__(self):
        super().__init__()
        self._kqueue = select.kqueue()

    def register_reader(self, fd, handler):
        if fd not in self.readers:
            kev = select.kevent(fd, select.KQ_FILTER_READ, select.KQ_EV_ADD)
            self._kqueue.control([kev], 0, 0)
        return super().register_reader(fd, handler)

    def register_writer(self, fd, handler):
        if fd not in self.writers:
            kev = select.kevent(fd, select.KQ_FILTER_WRITE, select.KQ_EV_ADD)
            self._kqueue.control([kev], 0, 0)
        return super().register_writer(fd, handler)

    def unregister_reader(self, fd):
        super().unregister_reader(fd)
        kev = select.kevent(fd, select.KQ_FILTER_READ, select.KQ_EV_DELETE)
        self._kqueue.control([kev], 0, 0)

    def unregister_writer(self, fd):
        super().unregister_writer(fd)
        kev = select.kevent(fd, select.KQ_FILTER_WRITE, select.KQ_EV_DELETE)
        self._kqueue.control([kev], 0, 0)

    def poll(self, timeout=None):
        events = []
        max_ev = len(self.readers) + len(self.writers)
        for kev in self._kqueue.control(None, max_ev, timeout):
            fd = kev.ident
            flag = kev.filter
            if flag == select.KQ_FILTER_READ and fd in self.readers:
                events.append(self.readers[fd])
            elif flag == select.KQ_FILTER_WRITE and fd in self.writers:
                events.append(self.writers[fd])
        return events


# Pick the best pollster class for the platform.
if hasattr(select, 'kqueue'):
    best_pollster = KqueuePollster
elif hasattr(select, 'epoll'):
    best_pollster = EPollPollster
elif hasattr(select, 'poll'):
    best_pollster = PollPollster
else:
    best_pollster = SelectPollster


class _StopError(BaseException):
    """Raised to stop the event loop."""


def _raise_stop_error():
    raise _StopError


class UnixEventLoop(events.EventLoop):
    """Unix event loop.

    See events.EventLoop for API specification.
    """

    def __init__(self, pollster=None):
        super().__init__()
        if pollster is None:
            logging.info('Using pollster: %s', best_pollster.__name__)
            pollster = best_pollster()
        self._pollster = pollster
        self._ready = collections.deque()  # [(callback, args), ...]
        self._scheduled = []  # [(when, callback, args), ...]
        self._default_executor = None
        self._make_self_pipe()

    def _make_self_pipe(self):
        # A self-socket, really. :-)
        self._ssock, self._csock = socketpair()
        self.add_reader(self._ssock.fileno(), self._read_from_self)

    def _read_from_self(self):
        self._ssock.recv(1)

    def _write_to_self(self):
        self._csock.send(b'x')

    def run(self):
        """Run the event loop until nothing left to do or stop() called.

        This keeps going as long as there are either readable and
        writable file descriptors, or scheduled callbacks (of either
        variety).

        TODO: Give this a timeout too?
        """
        while self._ready or self._scheduled or self._pollster.pollable() > 1:
            try:
                self._run_once()
            except _StopError:
                break

    def run_once(self, timeout=None):
        """Run through all callbacks and all I/O polls once."""
        try:
            self._run_once(timeout)
        except _StopError:
            pass

    def run_until_complete(self, future, timeout=None):
        """Run until the Future is done, or until a timeout.

        Return the Future's result, or raise its exception.  If the
        timeout is reached or stop() is called, raise TimeoutError.
        """
        if timeout is None:
            timeout = 0x7fffffff/1000.0  # 24 days
        future.add_done_callback(lambda _: self.stop())
        handler = self.call_later(timeout, _raise_stop_error)
        self.run()
        handler.cancel()
        if future.done():
            return future.result()  # May raise future.exception().
        else:
            raise futures.TimeoutError

    def stop(self):
        """Stop running the event loop.

        Every callback scheduled before stop() is called will run.
        Callback scheduled after stop() is called won't.  However,
        those callbacks will run if run() is called again later.
        """
        self.call_soon(_raise_stop_error)

    def call_later(self, delay, callback, *args):
        """Arrange for a callback to be called at a given time.

        Return an object with a cancel() method that can be used to
        cancel the call.

        The delay can be an int or float, expressed in seconds.  It is
        always a relative time.

        Each callback will be called exactly once.  If two callbacks
        are scheduled for exactly the same time, it undefined which
        will be called first.

        Events scheduled in the past are passed on to call_soon(), so
        these will be called in the order in which they were
        registered rather than by time due.  This is so you can't
        cheat and insert yourself at the front of the ready queue by
        using a negative time.

        Any positional arguments after the callback will be passed to
        the callback when it is called.

        # TODO: Should delay is None be interpreted as Infinity?
        """
        if delay <= 0:
            return self.call_soon(callback, *args)
        handler = events.Handler(time.monotonic() + delay, callback, args)
        heapq.heappush(self._scheduled, handler)
        return handler

    def call_repeatedly(self, interval, callback, *args):
        """Call a callback every 'interval' seconds."""
        def wrapper():
            callback(*args)  # If this fails, the chain is broken.
            handler._when = time.monotonic() + interval
            heapq.heappush(self._scheduled, handler)
        handler = events.Handler(time.monotonic() + interval, wrapper, ())
        heapq.heappush(self._scheduled, handler)
        return handler

    def call_soon(self, callback, *args):
        """Arrange for a callback to be called as soon as possible.

        This operates as a FIFO queue, callbacks are called in the
        order in which they are registered.  Each callback will be
        called exactly once.

        Any positional arguments after the callback will be passed to
        the callback when it is called.
        """
        handler = events.Handler(None, callback, args)
        self._ready.append(handler)
        return handler

    def call_soon_threadsafe(self, callback, *args):
        """XXX"""
        handler = self.call_soon(callback, *args)
        self._write_to_self()
        return handler

    def wrap_future(self, future):
        """XXX"""
        if isinstance(future, futures.Future):
            return future  # Don't wrap our own type of Future.
        new_future = futures.Future()
        future.add_done_callback(
            lambda future:
                self.call_soon_threadsafe(new_future._copy_state, future))
        return new_future

    def run_in_executor(self, executor, function, *args):
        if executor is None:
            executor = self._default_executor
            if executor is None:
                executor = concurrent.futures.ThreadPoolExecutor(_MAX_WORKERS)
                self._default_executor = executor
        return self.wrap_future(executor.submit(function, *args))

    def set_default_executor(self, executor):
        self._default_executor = executor

    def getaddrinfo(self, host, port, *,
                    family=0, type=0, proto=0, flags=0):
        return self.run_in_executor(None, socket.getaddrinfo,
                                    host, port, family, type, proto, flags)

    def getnameinfo(self, sockaddr, flags=0):
        return self.run_in_executor(None, socket.getnameinfo, sockaddr, flags)

    # TODO: Or create_connection()?
    def create_transport(self, protocol_factory, host, port, *,
                         family=0, type=0, proto=0, flags=0):
        """XXX"""

    def start_serving(self, protocol_factory, host, port, *,
                      family=0, type=0, proto=0, flags=0):
        """XXX"""

    def add_reader(self, fd, callback, *args):
        """Add a reader callback.  Return a Handler instance."""
        handler = events.Handler(None, callback, args)
        self._pollster.register_reader(fd, handler)
        return handler

    def remove_reader(self, fd):
        """Remove a reader callback."""
        if fd in self._pollster.readers:
            self._pollster.unregister_reader(fd)

    def add_writer(self, fd, callback, *args):
        """Add a writer callback.  Return a Handler instance."""
        handler = events.Handler(None, callback, args)
        self._pollster.register_writer(fd, handler)
        return handler

    def remove_writer(self, fd):
        """Remove a writer callback."""
        if fd in self._pollster.writers:
            self._pollster.unregister_writer(fd)

    def sock_recv(self, sock, n):
        """XXX"""
        fut = futures.Future()
        self._sock_recv(fut, False, sock, n)
        return fut

    def _sock_recv(self, fut, registered, sock, n):
        fd = sock.fileno()
        if registered:
            # Remove the callback early.  It should be rare that the
            # pollster says the fd is ready but the call still returns
            # EAGAIN, and I am willing to take a hit in that case in
            # order to simplify the common case.
            self.remove_reader(fd)
        if fut.cancelled():
            return
        try:
            data = sock.recv(n)
            fut.set_result(data)
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                fut.set_exception(exc)
            else:
                self.add_reader(fd, self._sock_recv, fut, True, sock, n)

    def sock_sendall(self, sock, data):
        """XXX"""
        fut = futures.Future()
        self._sock_sendall(fut, False, sock, data)
        return fut

    def _sock_sendall(self, fut, registered, sock, data):
        fd = sock.fileno()
        if registered:
            self.remove_writer(fd)
        if fut.cancelled():
            return
        n = 0
        try:
            if data:
                n = sock.send(data)
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                fut.set_exception(exc)
                return
        if n == len(data):
            fut.set_result(None)
        else:
            if n:
                data = data[n:]
            self.add_writer(fd, self._sock_sendall, fut, True, sock, data)

    def sock_connect(self, sock, address):
        """XXX"""
        # That address better not require a lookup!  We're not calling
        # self.getaddrinfo() for you here.  But verifying this is
        # complicated; the socket module doesn't have a pattern for
        # IPv6 addresses (there are too many forms, apparently).
        fut = futures.Future()
        self._sock_connect(fut, False, sock, address)
        return fut

    def _sock_connect(self, fut, registered, sock, address):
        fd = sock.fileno()
        if registered:
            self.remove_writer(fd)
        if fut.cancelled():
            return
        try:
            if not registered:
                # First time around.
                sock.connect(address)
            else:
                err = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if err != 0:
                    # Jump to the except clause below.
                    raise socket.error(err, 'Connect call failed')
            fut.set_result(None)
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                fut.set_exception(exc)
            else:
                self.add_writer(fd, self._sock_connect,
                                fut, True, sock, address)

    def sock_accept(self, sock):
        """XXX"""
        fut = futures.Future()
        self._sock_accept(fut, False, sock)
        return fut

    def _sock_accept(self, fut, registered, sock):
        fd = sock.fileno()
        if registered:
            self.remove_reader(fd)
        if fut.cancelled():
            return
        try:
            conn, address = sock.accept()
            conn.setblocking(False)
            fut.set_result((conn, address))
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                fut.set_exception(exc)
            else:
                self.add_reader(fd, self._sock_accept, fut, True, sock)

    def _add_callback(self, handler):
        """Add a Handler to ready or scheduled."""
        if handler.cancelled:
            return
        if handler.when is None:
            self._ready.append(handler)
        else:
            heapq.heappush(self._scheduled, handler)

    def _run_once(self, timeout=None):
        """Run one full iteration of the event loop.

        This calls all currently ready callbacks, polls for I/O,
        schedules the resulting callbacks, and finally schedules
        'call_later' callbacks.
        """
        # TODO: Break each of these into smaller pieces.
        # TODO: Refactor to separate the callbacks from the readers/writers.
        # TODO: As step 4, run everything scheduled by steps 1-3.
        # TODO: An alternative API would be to do the *minimal* amount
        # of work, e.g. one callback or one I/O poll.

        # This is the only place where callbacks are actually *called*.
        # All other places just add them to ready.
        # TODO: Ensure this loop always finishes, even if some
        # callbacks keeps registering more callbacks.
        while self._ready:
            handler = self._ready.popleft()
            if not handler.cancelled:
                try:
                    if handler.kwds:
                        handler.callback(*handler.args, **handler.kwds)
                    else:
                        handler.callback(*handler.args)
                except Exception:
                    logging.exception('Exception in callback %s %r',
                                      handler.callback, handler.args)

        # Remove delayed calls that were cancelled from head of queue.
        while self._scheduled and self._scheduled[0].cancelled:
            heapq.heappop(self._scheduled)

        # Inspect the poll queue.
        if self._pollster.pollable() > 1:
            if self._scheduled:
                when = self._scheduled[0].when
                timeout = max(0, when - time.monotonic())
            t0 = time.monotonic()
            events = self._pollster.poll(timeout)
            t1 = time.monotonic()
            argstr = '' if timeout is None else ' %.3f' % timeout
            if t1-t0 >= 1:
                level = logging.INFO
            else:
                level = logging.DEBUG
            logging.log(level, 'poll%s took %.3f seconds', argstr, t1-t0)
            for handler in events:
                self._add_callback(handler)

        # Handle 'later' callbacks that are ready.
        now = time.monotonic()
        while self._scheduled:
            handler = self._scheduled[0]
            if handler.when > now:
                break
            handler = heapq.heappop(self._scheduled)
            self.call_soon(handler.callback, *handler.args)
