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
import os
import select
import socket
import sys
import threading
import time

from . import events
from . import futures

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
        self.readers = {}  # {fd: token, ...}.
        self.writers = {}  # {fd: token, ...}.

    def pollable(self):
        """Return the number readers and writers currently registered."""
        # The event loop needs the number since it must subtract one for
        # the self-pipe.
        return len(self.readers) + len(self.writers)

    # Subclasses are expected to extend the add/remove methods.

    def register_reader(self, fd, token):
        """Add or update a reader for a file descriptor."""
        self.readers[fd] = token

    def register_writer(self, fd, token):
        """Add or update a writer for a file descriptor."""
        self.writers[fd] = token

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
        is a token previously passed to register_reader/writer().
        """
        raise NotImplementedError


class SelectPollster(PollsterBase):
    """Pollster implementation using select."""

    def poll(self, timeout=None):
        # TODO: Add connections to the third list since "connection
        # failed" doesn't make the socket writable on Windows.
        readable, writable, _ = select.select(self.readers, self.writers,
                                              [], timeout)
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

    def register_reader(self, fd, token):
        super().register_reader(fd, token)
        self._update(fd)

    def register_writer(self, fd, token):
        super().register_writer(fd, token)
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
            if flags & (select.POLLIN | select.POLLHUP):
                if fd in self.readers:
                    events.append(self.readers[fd])
            if flags & (select.POLLOUT | select.POLLHUP):
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

    def register_reader(self, fd, token):
        super().register_reader(fd, token)
        self._update(fd)

    def register_writer(self, fd, token):
        super().register_writer(fd, token)
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
            if eventmask & select.EPOLLIN:
                if fd in self.readers:
                    events.append(self.readers[fd])
            if eventmask & select.EPOLLOUT:
                if fd in self.writers:
                    events.append(self.writers[fd])
        return events


class KqueuePollster(PollsterBase):
    """Pollster implementation using kqueue."""

    def __init__(self):
        super().__init__()
        self._kqueue = select.kqueue()

    def register_reader(self, fd, token):
        if fd not in self.readers:
            kev = select.kevent(fd, select.KQ_FILTER_READ, select.KQ_EV_ADD)
            self._kqueue.control([kev], 0, 0)
        return super().register_reader(fd, token)

    def register_writer(self, fd, token):
        if fd not in self.writers:
            kev = select.kevent(fd, select.KQ_FILTER_WRITE, select.KQ_EV_ADD)
            self._kqueue.control([kev], 0, 0)
        return super().register_writer(fd, token)

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
        self._make_self_pipe_or_sock()

    def _make_self_pipe_or_sock(self):
        # TODO: Just always use socketpair().  See proactor branch.
        if sys.platform == 'win32':
            from . import winsocketpair
            self._ssock, self._csock = winsocketpair.socketpair()
            self._pollster.register_reader(self._ssock.fileno(),
                                           self._read_from_self_sock)
            self._write_to_self = self._write_to_self_sock
        else:
            self._pipe_read_fd, self._pipe_write_fd = os.pipe()  # Self-pipe.
            self._pollster.register_reader(self._pipe_read_fd,
                                           self._read_from_self_pipe)
            self._write_to_self = self._write_to_self_pipe

    def _read_from_self_sock(self):
        self._ssock.recv(1)

    def _write_to_self_sock(self):
        self._csock.send(b'x')

    def _read_from_self_pipe(self):
        os.read(self._pipe_read_fd, 1)

    def _write_to_self_pipe(self):
        os.write(self._pipe_write_fd, b'x')

    def run(self):
        """Run the event loop until there is no work left to do.

        This keeps going as long as there are either readable and
        writable file descriptors, or scheduled callbacks (of either
        variety).
        """
        while self._ready or self._scheduled or self._pollster.pollable() > 1:
            self._run_once()

    # TODO: stop()?

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
        """
        if delay <= 0:
            return self.call_soon(callback, *args)
        dcall = events.DelayedCall(time.monotonic() + delay, callback, args)
        heapq.heappush(self._scheduled, dcall)
        return dcall

    def call_soon(self, callback, *args):
        """Arrange for a callback to be called as soon as possible.

        This operates as a FIFO queue, callbacks are called in the
        order in which they are registered.  Each callback will be
        called exactly once.

        Any positional arguments after the callback will be passed to
        the callback when it is called.
        """
        dcall = events.DelayedCall(None, callback, args)
        self._ready.append(dcall)
        return dcall

    def call_soon_threadsafe(self, callback, *args):
        """XXX"""
        dcall = self.call_soon(callback, *args)
        self._write_to_self()
        return dcall

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
        """Add a reader callback.  Return a DelayedCall instance."""
        dcall = events.DelayedCall(None, callback, args)
        self._pollster.register_reader(fd, dcall)
        return dcall

    def remove_reader(self, fd):
        """Remove a reader callback."""
        if fd in self._pollster.readers:
            self._pollster.unregister_reader(fd)

    def add_writer(self, fd, callback, *args):
        """Add a writer callback.  Return a DelayedCall instance."""
        dcall = events.DelayedCall(None, callback, args)
        self._pollster.register_writer(fd, dcall)
        return dcall

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

    def _add_callback(self, dcall):
        """Add a DelayedCall to ready or scheduled."""
        if dcall.cancelled:
            return
        if dcall.when is None:
            self._ready.append(dcall)
        else:
            heapq.heappush(self._scheduled, dcall)

    # TODO: Make this public?
    # TODO: Guarantee ready queue is empty on exit?
    def _run_once(self):
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
        while self._ready:
            dcall = self._ready.popleft()
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
        while self._scheduled and self._scheduled[0].cancelled:
            heapq.heappop(self._scheduled)

        # Inspect the poll queue.
        if self._pollster.pollable() > 1:
            if self._scheduled:
                when = self._scheduled[0].when
                timeout = max(0, when - time.monotonic())
            else:
                timeout = None
            t0 = time.monotonic()
            events = self._pollster.poll(timeout)
            t1 = time.monotonic()
            argstr = '' if timeout is None else ' %.3f' % timeout
            if t1-t0 >= 1:
                level = logging.INFO
            else:
                level = logging.DEBUG
            logging.log(level, 'poll%s took %.3f seconds', argstr, t1-t0)
            for dcall in events:
                self._add_callback(dcall)

        # Handle 'later' callbacks that are ready.
        now = time.monotonic()
        while self._scheduled:
            dcall = self._scheduled[0]
            if dcall.when > now:
                break
            dcall = heapq.heappop(self._scheduled)
            self.call_soon(dcall.callback, *dcall.args)
