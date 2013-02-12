"""Base implementation of event loop.

The event loop can be broken up into a multiplexer (the part
responsible for notifying us of IO events) and the event loop proper,
which wraps a multiplexer with functionality for scheduling callbacks,
immediately or at a given time in the future.

Whenever a public API takes a callback, subsequent positional
arguments will be passed to the callback if/when it is called.  This
avoids the proliferation of trivial lambdas implementing closures.
Keyword arguments for the callback are not supported; this is a
conscious design decision, leaving the door open for keyword arguments
to modify the meaning of the API call itself.
"""


import collections
import concurrent.futures
import heapq
import logging
import socket
import time

from . import events
from . import futures
from . import tasks


__all__ = ['BaseEventLoop']


# Argument for default thread pool executor creation.
_MAX_WORKERS = 5


class _StopError(BaseException):
    """Raised to stop the event loop."""


def _raise_stop_error(*args):
    raise _StopError


class BaseEventLoop(events.AbstractEventLoop):

    def __init__(self):
        self._ready = collections.deque()
        self._scheduled = []
        self._default_executor = None
        self._signal_handlers = {}

    def _make_socket_transport(self, event_loop, sock, protocol, waiter=None):
        """Create socket transport."""
        raise NotImplementedError

    def _make_ssl_transport(self, event_loop, rawsock,
                            protocol, sslcontext, waiter):
        """Create SSL transport."""
        raise NotImplementedError

    def run(self):
        """Run the event loop until nothing left to do or stop() called.

        This keeps going as long as there are either readable and
        writable file descriptors, or scheduled callbacks (of either
        variety).

        TODO: Give this a timeout too?
        """
        while (self._ready or
               self._scheduled or
               self._selector.registered_count() > 1):
            try:
                self._run_once()
            except _StopError:
                break

    def run_forever(self):
        """Run until stop() is called.

        This only makes sense over run() if you have another thread
        scheduling callbacks using call_soon_threadsafe().
        """
        handler = self.call_repeatedly(24*3600, lambda: None)
        try:
            self.run()
        finally:
            handler.cancel()

    def run_once(self, timeout=None):
        """Run through all callbacks and all I/O polls once.

        Calling stop() will break out of this too.
        """
        try:
            self._run_once(timeout)
        except _StopError:
            pass

    def run_until_complete(self, future, timeout=None):
        """Run until the Future is done, or until a timeout.

        Return the Future's result, or raise its exception.  If the
        timeout is reached or stop() is called, raise TimeoutError.
        """
        handler_called = False
        def stop_loop():
            nonlocal handler_called
            handler_called = True
            raise _StopError
        future.add_done_callback(_raise_stop_error)
        if timeout is None:
            self.run_forever()
        else:
            handler = self.call_later(timeout, stop_loop)
            self.run()
            handler.cancel()
        if handler_called:
            raise futures.TimeoutError
        return future.result()

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

        Callbacks scheduled in the past are passed on to call_soon(),
        so these will be called in the order in which they were
        registered rather than by time due.  This is so you can't
        cheat and insert yourself at the front of the ready queue by
        using a negative time.

        Any positional arguments after the callback will be passed to
        the callback when it is called.

        # TODO: Should delay is None be interpreted as Infinity?
        """
        if delay <= 0:
            return self.call_soon(callback, *args)
        handler = events.Timer(time.monotonic() + delay, callback, args)
        heapq.heappush(self._scheduled, handler)
        return handler

    def call_repeatedly(self, interval, callback, *args):
        """Call a callback every 'interval' seconds."""
        def wrapper():
            callback(*args)  # If this fails, the chain is broken.
            handler._when = time.monotonic() + interval
            heapq.heappush(self._scheduled, handler)
        handler = events.Timer(time.monotonic() + interval, wrapper, ())
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
        handler = events.make_handler(callback, args)
        self._ready.append(handler)
        return handler

    def call_soon_threadsafe(self, callback, *args):
        """XXX"""
        handler = self.call_soon(callback, *args)
        self._write_to_self()
        return handler

    def run_in_executor(self, executor, callback, *args):
        if isinstance(callback, events.Handler):
            assert not args
            assert not isinstance(callback, events.Timer)
            if callback.cancelled:
                f = futures.Future()
                f.set_result(None)
                return f
            callback, args = callback.callback, callback.args
        if executor is None:
            executor = self._default_executor
            if executor is None:
                executor = concurrent.futures.ThreadPoolExecutor(_MAX_WORKERS)
                self._default_executor = executor
        return self.wrap_future(executor.submit(callback, *args))

    def set_default_executor(self, executor):
        self._default_executor = executor

    def getaddrinfo(self, host, port, *,
                    family=0, type=0, proto=0, flags=0):
        return self.run_in_executor(None, socket.getaddrinfo,
                                    host, port, family, type, proto, flags)

    def getnameinfo(self, sockaddr, flags=0):
        return self.run_in_executor(None, socket.getnameinfo, sockaddr, flags)

    @tasks.task
    def create_connection(self, protocol_factory, host=None, port=None, *,
                          ssl=False, family=0, proto=0, flags=0, sock=None):
        """XXX"""
        if host is not None or port is not None:
            if sock is not None:
                raise ValueError(
                    "host, port and sock can not be specified at the same time")

            infos = yield from self.getaddrinfo(
                host, port, family=family,
                type=socket.SOCK_STREAM, proto=proto, flags=flags)

            if not infos:
                raise socket.error('getaddrinfo() returned empty list')

            exceptions = []
            for family, type, proto, cname, address in infos:
                sock = None
                try:
                    sock = socket.socket(family=family, type=type, proto=proto)
                    sock.setblocking(False)
                    yield self.sock_connect(sock, address)
                except socket.error as exc:
                    if sock is not None:
                        sock.close()
                    exceptions.append(exc)
                else:
                    break
            else:
                if len(exceptions) == 1:
                    raise exceptions[0]
                else:
                    # If they all have the same str(), raise one.
                    model = str(exceptions[0])
                    if all(str(exc) == model for exc in exceptions):
                        raise exceptions[0]
                    # Raise a combined exception so the user can see all
                    # the various error messages.
                    raise socket.error('Multiple exceptions: {}'.format(
                        ', '.join(str(exc) for exc in exceptions)))

        elif sock is None:
            raise ValueError(
                "host and port was not specified and no sock specified")

        protocol = protocol_factory()
        waiter = futures.Future()
        if ssl:
            sslcontext = None if isinstance(ssl, bool) else ssl
            transport = self._make_ssl_transport(
                self, sock, protocol, sslcontext, waiter)
        else:
            transport = self._make_socket_transport(
                self, sock, protocol, waiter)

        yield from waiter
        return transport, protocol

    # TODO: Or create_server()?
    @tasks.task
    def start_serving(self, protocol_factory, host=None, port=None, *,
                      family=0, proto=0, flags=0, backlog=100, sock=None):
        """XXX"""
        if host is not None or port is not None:
            if sock is not None:
                raise ValueError(
                    "host, port and sock can not be specified at the same time")

            infos = yield from self.getaddrinfo(
                host, port, family=family,
                type=socket.SOCK_STREAM, proto=proto, flags=flags)

            if not infos:
                raise socket.error('getaddrinfo() returned empty list')

            # TODO: Maybe we want to bind every address in the list
            # instead of the first one that works?
            exceptions = []
            for family, type, proto, cname, address in infos:
                sock = socket.socket(family=family, type=type, proto=proto)
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    sock.bind(address)
                except socket.error as exc:
                    sock.close()
                    exceptions.append(exc)
                else:
                    break
            else:
                raise exceptions[0]

        elif sock is None:
            raise ValueError(
                "host and port was not specified and no sock specified")

        sock.listen(backlog)
        sock.setblocking(False)
        self._start_serving(protocol_factory, sock)
        return sock

    def _add_callback(self, handler):
        """Add a Handler to ready or scheduled."""
        if handler.cancelled:
            return
        if isinstance(handler, events.Timer):
            heapq.heappush(self._scheduled, handler)
        else:
            self._ready.append(handler)

    def wrap_future(self, future):
        """XXX"""
        if isinstance(future, futures.Future):
            return future  # Don't wrap our own type of Future.
        new_future = futures.Future()
        future.add_done_callback(
            lambda future:
                self.call_soon_threadsafe(new_future._copy_state, future))
        return new_future

    def _run_once(self, timeout=None):
        """Run one full iteration of the event loop.

        This calls all currently ready callbacks, polls for I/O,
        schedules the resulting callbacks, and finally schedules
        'call_later' callbacks.
        """
        # TODO: Break each of these into smaller pieces.
        # TODO: Refactor to separate the callbacks from the readers/writers.
        # TODO: An alternative API would be to do the *minimal* amount
        # of work, e.g. one callback or one I/O poll.

        # Remove delayed calls that were cancelled from head of queue.
        while self._scheduled and self._scheduled[0].cancelled:
            heapq.heappop(self._scheduled)

        # Inspect the poll queue.  If there's exactly one selectable
        # file descriptor, it's the self-pipe, and if there's nothing
        # scheduled, we should ignore it.
        if self._selector.registered_count() > 1 or self._scheduled:
            if self._ready:
                timeout = 0
            elif self._scheduled:
                # Compute the desired timeout.
                when = self._scheduled[0].when
                deadline = max(0, when - time.monotonic())
                if timeout is None:
                    timeout = deadline
                else:
                    timeout = min(timeout, deadline)

            t0 = time.monotonic()
            event_list = self._selector.select(timeout)
            t1 = time.monotonic()
            argstr = '' if timeout is None else ' %.3f' % timeout
            if t1-t0 >= 1:
                level = logging.INFO
            else:
                level = logging.DEBUG
            logging.log(level, 'poll%s took %.3f seconds', argstr, t1-t0)
            self._process_events(event_list)

        # Handle 'later' callbacks that are ready.
        now = time.monotonic()
        while self._scheduled:
            handler = self._scheduled[0]
            if handler.when > now:
                break
            handler = heapq.heappop(self._scheduled)
            self._ready.append(handler)

        # This is the only place where callbacks are actually *called*.
        # All other places just add them to ready.
        # Note: We run all currently scheduled callbacks, but not any
        # callbacks scheduled by callbacks run this time around --
        # they will be run the next time (after another I/O poll).
        # Use an idiom that is threadsafe without using locks.
        ntodo = len(self._ready)
        for i in range(ntodo):
            handler = self._ready.popleft()
            if not handler.cancelled:
                try:
                    handler.callback(*handler.args)
                except Exception:
                    logging.exception('Exception in callback %s %r',
                                      handler.callback, handler.args)
