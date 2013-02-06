"""Event loop using a proactor and related classes.

A proactor is a "notify-on-completion" multiplexer.  Currently a
proactor is only implemented on Windows with IOCP.
"""

import logging


from . import base_events
from . import transports
from . import winsocketpair


class _ProactorSocketTransport(transports.Transport):

    def __init__(self, event_loop, sock, protocol, waiter=None):
        self._event_loop = event_loop
        self._sock = sock
        self._protocol = protocol
        self._buffer = []
        self._read_fut = None
        self._write_fut = None
        self._closing = False  # Set when close() called.
        self._event_loop.call_soon(self._protocol.connection_made, self)
        self._event_loop.call_soon(self._loop_reading)
        if waiter is not None:
            self._event_loop.call_soon(waiter.set_result, None)

    def _loop_reading(self, f=None):
        try:
            assert f is self._read_fut
            if f:
                data = f.result()
                if not data:
                    self._event_loop.call_soon(self._protocol.eof_received)
                    self._read_fut = None
                    return
                self._event_loop.call_soon(self._protocol.data_received, data)
            self._read_fut = self._event_loop._proactor.recv(self._sock, 4096)
        except ConnectionAbortedError as exc:
            if not self._closing:
                self._fatal_error(exc)
        except OSError as exc:
            self._fatal_error(exc)
        else:
            self._read_fut.add_done_callback(self._loop_reading)

    def write(self, data):
        assert isinstance(data, bytes)
        assert not self._closing
        if not data:
            return
        self._buffer.append(data)
        if not self._write_fut:
            self._loop_writing()

    def _loop_writing(self, f=None):
        try:
            assert f is self._write_fut
            if f:
                f.result()
            data = b''.join(self._buffer)
            self._buffer = []
            if not data:
                self._write_fut = None
                return
            self._write_fut = self._event_loop._proactor.send(self._sock, data)
        except OSError as exc:
            self._fatal_error(exc)
        else:
            self._write_fut.add_done_callback(self._loop_writing)

    # TODO: write_eof(), can_write_eof().

    def abort(self):
        self._fatal_error(None)

    def close(self):
        self._closing = True
        if self._write_fut:
            self._write_fut.cancel()
        if not self._buffer:
            self._event_loop.call_soon(self._call_connection_lost, None)

    def _fatal_error(self, exc):
        logging.exception('Fatal error for %s', self)
        if self._write_fut:
            self._write_fut.cancel()
        if self._read_fut:            # XXX
            self._read_fut.cancel()
        self._write_fut = self._read_fut = None
        self._buffer = []
        self._event_loop.call_soon(self._call_connection_lost, exc)

    def _call_connection_lost(self, exc):
        try:
            self._protocol.connection_lost(exc)
        finally:
            self._sock.close()


class BaseProactorEventLoop(base_events.BaseEventLoop):

    SocketTransport = _ProactorSocketTransport

    @staticmethod
    def SslTransport(*args, **kwds):
        raise NotImplementedError

    def __init__(self, proactor):
        super().__init__()
        logging.debug('Using proactor: %s', proactor.__class__.__name__)
        self._proactor = proactor
        self._selector = proactor   # convenient alias
        self._make_self_pipe()

    def close(self):
        if self._proactor is not None:
            self._proactor.close()
            self._proactor = None
            self._selector = None
        self._ssock.close()
        self._csock.close()

    def sock_recv(self, sock, n):
        return self._proactor.recv(sock, n)

    def sock_sendall(self, sock, data):
        return self._proactor.send(sock, data)

    def sock_connect(self, sock, address):
        return self._proactor.connect(sock, address)

    def sock_accept(self, sock):
        return self._proactor.accept(sock)

    def _socketpair(self):
        raise NotImplementedError

    def _make_self_pipe(self):
        # A self-socket, really. :-)
        self._ssock, self._csock = self._socketpair()
        self._ssock.setblocking(False)
        self._csock.setblocking(False)
        def loop(f=None):
            try:
                if f:
                    f.result()      # may raise
                f = self._proactor.recv(self._ssock, 4096)
            except:
                self.close()
                raise
            else:
                f.add_done_callback(loop)
        self.call_soon(loop)

    def _write_to_self(self):
        self._csock.send(b'x')

    def _start_serving(self, protocol_factory, sock):
        def loop(f=None):
            try:
                if f:
                    conn, addr = f.result()
                    protocol = protocol_factory()
                    transport = self.SocketTransport(self, conn, protocol)
                f = self._proactor.accept(sock)
            except OSError as exc:
                sock.close()
                logging.exception('Accept failed')
            else:
                f.add_done_callback(loop)
        self.call_soon(loop)

    def _process_events(self, event_list):
        pass    # XXX hard work currently done in poll
