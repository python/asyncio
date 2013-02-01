"""UNIX event loop and related classes.

The event loop can be broken up into a selector (the part responsible
for telling us when file descriptors are ready) and the event loop
proper, which wraps a selector with functionality for scheduling
callbacks, immediately or at a given time in the future.

Whenever a public API takes a callback, subsequent positional
arguments will be passed to the callback if/when it is called.  This
avoids the proliferation of trivial lambdas implementing closures.
Keyword arguments for the callback are not supported; this is a
conscious design decision, leaving the door open for keyword arguments
to modify the meaning of the API call itself.
"""

import collections
import concurrent.futures
import errno
import heapq
import logging
import select
import socket
try:
    import ssl
except ImportError:
    ssl = None
import sys
import threading
import time

try:
    import signal
except ImportError:
    signal = None

from . import events
from . import futures
from . import protocols
from . import selectors
from . import tasks
from . import transports

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


class _StopError(BaseException):
    """Raised to stop the event loop."""


def _raise_stop_error():
    raise _StopError


class UnixEventLoop(events.EventLoop):
    """Unix event loop.

    See events.EventLoop for API specification.
    """

    def __init__(self, selector=None):
        super().__init__()
        if selector is None:
            # pick the best selector class for the platform
            selector = selectors.Selector()
            logging.debug('Using selector: %s', selector.__class__.__name__)
        self._selector = selector
        self._ready = collections.deque()
        self._scheduled = []
        self._default_executor = None
        self._signal_handlers = {}
        self._make_self_pipe()

    def close(self):
        if self._selector is not None:
            self._selector.close()
            self._selector = None
        self._ssock.close()
        self._csock.close()

    def _make_self_pipe(self):
        # A self-socket, really. :-)
        self._ssock, self._csock = socketpair()
        self._ssock.setblocking(False)
        self._csock.setblocking(False)
        self.add_reader(self._ssock.fileno(), self._read_from_self)

    def _read_from_self(self):
        try:
            self._ssock.recv(1)
        except socket.error as exc:
            if exc.errno in _TRYAGAIN:
                return
            raise  # Halp!

    def _write_to_self(self):
        try:
            self._csock.send(b'x')
        except socket.error as exc:
            if exc.errno in _TRYAGAIN:
                return
            raise  # Halp!

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
        handler = events.make_handler(time.monotonic() + delay, callback, args)
        heapq.heappush(self._scheduled, handler)
        return handler

    def call_repeatedly(self, interval, callback, *args):
        """Call a callback every 'interval' seconds."""
        def wrapper():
            callback(*args)  # If this fails, the chain is broken.
            handler._when = time.monotonic() + interval
            heapq.heappush(self._scheduled, handler)
        handler = events.make_handler(time.monotonic() + interval, wrapper, ())
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
        handler = events.make_handler(None, callback, args)
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

    def run_in_executor(self, executor, callback, *args):
        if isinstance(callback, events.Handler):
            assert not args
            assert callback.when is None
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
            transport = _UnixSslTransport(
                self, sock, protocol, sslcontext, waiter)
        else:
            transport = _UnixSocketTransport(
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
        self.add_reader(sock.fileno(), self._accept_connection,
                        protocol_factory, sock)
        return sock

    def _accept_connection(self, protocol_factory, sock):
        try:
            conn, addr = sock.accept()
        except socket.error as exc:
            if exc.errno in _TRYAGAIN:
                return  # False alarm.
            # Bad error.  Stop serving.
            self.remove_reader(sock.fileno())
            sock.close()
            # There's nowhere to send the error, so just log it.
            # TODO: Someone will want an error handler for this.
            logging.exception('Accept failed')
            return
        protocol = protocol_factory()
        transport = _UnixSocketTransport(self, conn, protocol)
        # It's now up to the protocol to handle the connection.

    def add_reader(self, fd, callback, *args):
        """Add a reader callback.  Return a Handler instance."""
        handler = events.make_handler(None, callback, args)
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            self._selector.register(fd, selectors.EVENT_READ,
                                    (handler, None, None))
        else:
            self._selector.modify(fd, mask | selectors.EVENT_READ,
                                  (handler, writer, connector))

        return handler

    def remove_reader(self, fd):
        """Remove a reader callback."""
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            return False
        else:
            mask &= ~selectors.EVENT_READ
            if not mask:
                self._selector.unregister(fd)
            else:
                self._selector.modify(fd, mask, (None, writer, connector))
            if reader is not None:
                reader.cancel()
            return True

    def add_writer(self, fd, callback, *args):
        """Add a writer callback.  Return a Handler instance."""
        handler = events.make_handler(None, callback, args)
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            self._selector.register(fd, selectors.EVENT_WRITE,
                                    (None, handler, None))
        else:
            # Remove connector.
            mask &= ~selectors.EVENT_CONNECT
            self._selector.modify(fd, mask | selectors.EVENT_WRITE,
                                  (reader, handler, None))
        return handler

    def remove_writer(self, fd):
        """Remove a writer callback."""
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            return False
        else:
            # Remove both writer and connector.
            mask &= ~(selectors.EVENT_WRITE | selectors.EVENT_CONNECT)
            if not mask:
                self._selector.unregister(fd)
            else:
                self._selector.modify(fd, mask, (reader, None, None))
            if writer is not None:
                writer.cancel()
            if connector is not None:
                connector.cancel()
            return True

    # NOTE: add_connector() and add_writer() are mutually exclusive.
    # While you can independently manipulate readers and writers,
    # adding a connector for a particular FD automatically removes the
    # writer for that FD, and vice versa, and removing a writer or a
    # connector actually removes both writer and connector.  This is
    # because in most cases writers and connectors use the same mode
    # for the platform polling function; the distinction is only
    # important for PollSelector() on Windows.

    def add_connector(self, fd, callback, *args):
        """Add a connector callback.  Return a Handler instance."""
        handler = events.make_handler(None, callback, args)
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            self._selector.register(fd, selectors.EVENT_CONNECT,
                                    (None, None, handler))
        else:
            # Remove writer.
            mask &= ~selectors.EVENT_WRITE
            self._selector.modify(fd, mask | selectors.EVENT_CONNECT,
                                  (reader, None, handler))
        return handler

    def remove_connector(self, fd):
        """Remove a connector callback."""
        try:
            mask, (reader, writer, connector) = self._selector.get_info(fd)
        except KeyError:
            return False
        else:
            # Remove both writer and connector.
            mask &= ~(selectors.EVENT_WRITE | selectors.EVENT_CONNECT)
            if not mask:
                self._selector.unregister(fd)
            else:
                self._selector.modify(fd, mask, (reader, None, None))
            if writer is not None:
                writer.cancel()
            if connector is not None:
                connector.cancel()
            return True

    def sock_recv(self, sock, n):
        """XXX"""
        fut = futures.Future()
        self._sock_recv(fut, False, sock, n)
        return fut

    def _sock_recv(self, fut, registered, sock, n):
        fd = sock.fileno()
        if registered:
            # Remove the callback early.  It should be rare that the
            # selector says the fd is ready but the call still returns
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
            self.remove_connector(fd)
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
                self.add_connector(fd, self._sock_connect,
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

    def add_signal_handler(self, sig, callback, *args):
        """Add a handler for a signal.  UNIX only.

        Raise ValueError if the signal number is invalid or uncatchable.
        Raise RuntimeError if there is a problem setting up the handler.
        """
        self._check_signal(sig)
        try:
            # set_wakeup_fd() raises ValueError if this is not the
            # main thread.  By calling it early we ensure that an
            # event loop running in another thread cannot add a signal
            # handler.
            signal.set_wakeup_fd(self._csock.fileno())
        except ValueError as exc:
            raise RuntimeError(str(exc))
        handler = events.make_handler(None, callback, args)
        self._signal_handlers[sig] = handler
        try:
            signal.signal(sig, self._handle_signal)
        except OSError as exc:
            del self._signal_handlers[sig]
            if not self._signal_handlers:
                try:
                    signal.set_wakeup_fd(-1)
                except ValueError as nexc:
                    logging.info('set_wakeup_fd(-1) failed: %s', nexc)
            if exc.errno == errno.EINVAL:
                raise RuntimeError('sig {} cannot be caught'.format(sig))
            else:
                raise
        return handler

    def _handle_signal(self, sig, arg):
        """Internal helper that is the actual signal handler."""
        handler = self._signal_handlers.get(sig)
        if handler is None:
            return  # Assume it's some race condition.
        if handler.cancelled:
            self.remove_signal_handler(sig)  # Remove it properly.
        else:
            self.call_soon_threadsafe(handler.callback, *handler.args)

    def remove_signal_handler(self, sig):
        """Remove a handler for a signal.  UNIX only.

        Return True if a signal handler was removed, False if not."""
        self._check_signal(sig)
        try:
            del self._signal_handlers[sig]
        except KeyError:
            return False
        if sig == signal.SIGINT:
            handler = signal.default_int_handler
        else:
            handler = signal.SIG_DFL
        try:
            signal.signal(sig, handler)
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                raise RuntimeError('sig {} cannot be caught'.format(sig))
            else:
                raise
        if not self._signal_handlers:
            try:
                signal.set_wakeup_fd(-1)
            except ValueError as exc:
                logging.info('set_wakeup_fd(-1) failed: %s', exc)
        return True

    def _check_signal(self, sig):
        """Internal helper to validate a signal.

        Raise ValueError if the signal number is invalid or uncatchable.
        Raise RuntimeError if there is a problem setting up the handler.
        """
        if not isinstance(sig, int):
            raise TypeError('sig must be an int, not {!r}'.format(sig))
        if signal is None:
            raise RuntimeError('Signals are not supported')
        if not (1 <= sig < signal.NSIG):
            raise ValueError('sig {} out of range(1, {})'.format(sig,
                                                                 signal.NSIG))
        if sys.platform == 'win32':
            raise RuntimeError('Signals are not really supported on Windows')

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
            for fileobj, mask, (reader, writer, connector) in event_list:
                if mask & selectors.EVENT_READ and reader is not None:
                    if reader.cancelled:
                        self.remove_reader(fileobj)
                    else:
                        self._add_callback(reader)
                if mask & selectors.EVENT_WRITE and writer is not None:
                    if writer.cancelled:
                        self.remove_writer(fileobj)
                    else:
                        self._add_callback(writer)
                elif mask & selectors.EVENT_CONNECT and connector is not None:
                    if connector.cancelled:
                        self.remove_connector(fileobj)
                    else:
                        self._add_callback(connector)

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


class _UnixSocketTransport(transports.Transport):

    def __init__(self, event_loop, sock, protocol, waiter=None):
        self._event_loop = event_loop
        self._sock = sock
        self._protocol = protocol
        self._buffer = []
        self._closing = False  # Set when close() called.
        self._event_loop.add_reader(self._sock.fileno(), self._read_ready)
        self._event_loop.call_soon(self._protocol.connection_made, self)
        if waiter is not None:
            self._event_loop.call_soon(waiter.set_result, None)

    def _read_ready(self):
        try:
            data = self._sock.recv(16*1024)
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                self._fatal_error(exc)
        else:
            if data:
                self._event_loop.call_soon(self._protocol.data_received, data)
            else:
                self._event_loop.remove_reader(self._sock.fileno())
                self._event_loop.call_soon(self._protocol.eof_received)


    def write(self, data):
        assert isinstance(data, bytes)
        assert not self._closing
        if not data:
            return
        if not self._buffer:
            # Attempt to send it right away first.
            try:
                n = self._sock.send(data)
            except socket.error as exc:
                if exc.errno in _TRYAGAIN:
                    n = 0
                else:
                    self._fatal_error(exc)
                    return
            if n == len(data):
                return
            if n:
                data = data[n:]
            self._event_loop.add_writer(self._sock.fileno(), self._write_ready)
        self._buffer.append(data)

    def _write_ready(self):
        data = b''.join(self._buffer)
        self._buffer = []
        try:
            if data:
                n = self._sock.send(data)
            else:
                n = 0
        except socket.error as exc:
            if exc.errno in _TRYAGAIN:
                n = 0
            else:
                self._fatal_error(exc)
                return
        if n == len(data):
            self._event_loop.remove_writer(self._sock.fileno())
            if self._closing:
                self._event_loop.call_soon(self._call_connection_lost, None)
            return
        if n:
            data = data[n:]
        self._buffer.append(data)  # Try again later.

    # TODO: write_eof(), can_write_eof().

    def abort(self):
        self._fatal_error(None)

    def close(self):
        self._closing = True
        self._event_loop.remove_reader(self._sock.fileno())
        if not self._buffer:
            self._event_loop.call_soon(self._call_connection_lost, None)

    def _fatal_error(self, exc):
        logging.exception('Fatal error for %s', self)
        self._event_loop.remove_writer(self._sock.fileno())
        self._event_loop.remove_reader(self._sock.fileno())
        self._buffer = []
        self._event_loop.call_soon(self._call_connection_lost, exc)

    def _call_connection_lost(self, exc):
        try:
            self._protocol.connection_lost(exc)
        finally:
            self._sock.close()


class _UnixSslTransport(transports.Transport):

    def __init__(self, event_loop, rawsock, protocol, sslcontext, waiter):
        self._event_loop = event_loop
        self._rawsock = rawsock
        self._protocol = protocol
        sslcontext = sslcontext or ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        self._sslcontext = sslcontext
        self._waiter = waiter
        sslsock = sslcontext.wrap_socket(rawsock,
                                         do_handshake_on_connect=False)
        self._sslsock = sslsock
        self._buffer = []
        self._closing = False  # Set when close() called.
        self._on_handshake()

    def _on_handshake(self):
        fd = self._sslsock.fileno()
        try:
            self._sslsock.do_handshake()
        except ssl.SSLWantReadError:
            self._event_loop.add_reader(fd, self._on_handshake)
            return
        except ssl.SSLWantWriteError:
            self._event_loop.add_writer(fd, self._on_handshake)
            return
        except Exception as exc:
            self._sslsock.close()
            self._waiter.set_exception(exc)
            return
        except BaseException as exc:
            self._sslsock.close()
            self._waiter.set_exception(exc)
            raise
        self._event_loop.remove_reader(fd)
        self._event_loop.remove_writer(fd)
        self._event_loop.add_reader(fd, self._on_ready)
        self._event_loop.add_writer(fd, self._on_ready)
        self._event_loop.call_soon(self._protocol.connection_made, self)
        self._event_loop.call_soon(self._waiter.set_result, None)

    def _on_ready(self):
        # Because of renegotiations (?), there's no difference between
        # readable and writable.  We just try both.  XXX This may be
        # incorrect; we probably need to keep state about what we
        # should do next.

        # Maybe we're already closed...
        fd = self._sslsock.fileno()
        if fd < 0:
            return

        # First try reading.
        try:
            data = self._sslsock.recv(8192)
        except ssl.SSLWantReadError:
            pass
        except ssl.SSLWantWriteError:
            pass
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                self._fatal_error(exc)
                return
        else:
            if data:
                self._protocol.data_received(data)
            else:
                # TODO: Don't close when self._buffer is non-empty.
                assert not self._buffer
                self._event_loop.remove_reader(fd)
                self._event_loop.remove_writer(fd)
                self._sslsock.close()
                self._protocol.connection_lost(None)
                return

        # Now try writing, if there's anything to write.
        if not self._buffer:
            return

        data = b''.join(self._buffer)
        self._buffer = []
        try:
            n = self._sslsock.send(data)
        except ssl.SSLWantReadError:
            pass
        except ssl.SSLWantWriteError:
            pass
        except socket.error as exc:
            if exc.errno not in _TRYAGAIN:
                self._fatal_error(exc)
                return
        else:
            if n < len(data):
                self._buffer.append(data[n:])

    def write(self, data):
        assert isinstance(data, bytes)
        assert not self._closing
        if not data:
            return
        self._buffer.append(data)
        # We could optimize, but the callback can do this for now.

    # TODO: write_eof(), can_write_eof().

    def abort(self):
        self._fatal_error(None)

    def close(self):
        self._closing = True
        self._event_loop.remove_reader(self._sslsock.fileno())
        if not self._buffer:
            self._event_loop.call_soon(self._protocol.connection_lost, None)

    def _fatal_error(self, exc):
        logging.exception('Fatal error for %s', self)
        self._event_loop.remove_writer(self._sslsock.fileno())
        self._event_loop.remove_reader(self._sslsock.fileno())
        self._buffer = []
        self._event_loop.call_soon(self._protocol.connection_lost, exc)
