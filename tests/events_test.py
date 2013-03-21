"""Tests for events.py."""

import concurrent.futures
import contextlib
import errno
import fcntl
import gc
import io
import os
import re
import signal
import socket
try:
    import ssl
except ImportError:
    ssl = None
import sys
import threading
import time
import unittest
import unittest.mock

from wsgiref.simple_server import make_server, WSGIRequestHandler, WSGIServer

from tulip import events
from tulip import transports
from tulip import protocols
from tulip import selector_events
from tulip import tasks
from tulip import test_utils


class MyProto(protocols.Protocol):

    def __init__(self):
        self.state = 'INITIAL'
        self.nbytes = 0

    def connection_made(self, transport):
        self.transport = transport
        assert self.state == 'INITIAL', self.state
        self.state = 'CONNECTED'
        transport.write(b'GET / HTTP/1.0\r\nHost: xkcd.com\r\n\r\n')

    def data_received(self, data):
        assert self.state == 'CONNECTED', self.state
        self.nbytes += len(data)

    def eof_received(self):
        assert self.state == 'CONNECTED', self.state
        self.state = 'EOF'
        self.transport.close()

    def connection_lost(self, exc):
        assert self.state in ('CONNECTED', 'EOF'), self.state
        self.state = 'CLOSED'


class MyDatagramProto(protocols.DatagramProtocol):

    def __init__(self):
        self.state = 'INITIAL'
        self.nbytes = 0

    def connection_made(self, transport):
        self.transport = transport
        assert self.state == 'INITIAL', self.state
        self.state = 'INITIALIZED'

    def datagram_received(self, data, addr):
        assert self.state == 'INITIALIZED', self.state
        self.nbytes += len(data)

    def connection_refused(self, exc):
        assert self.state == 'INITIALIZED', self.state

    def connection_lost(self, exc):
        assert self.state == 'INITIALIZED', self.state
        self.state = 'CLOSED'


class MyReadPipeProto(protocols.Protocol):

    def __init__(self):
        self.state = ['INITIAL']
        self.nbytes = 0
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        assert self.state == ['INITIAL'], self.state
        self.state.append('CONNECTED')

    def data_received(self, data):
        assert self.state == ['INITIAL', 'CONNECTED'], self.state
        self.nbytes += len(data)

    def eof_received(self):
        assert self.state == ['INITIAL', 'CONNECTED'], self.state
        self.state.append('EOF')
        self.transport.close()

    def connection_lost(self, exc):
        assert self.state == ['INITIAL', 'CONNECTED', 'EOF'], self.state
        self.state.append('CLOSED')


class MyWritePipeProto(protocols.Protocol):

    def __init__(self):
        self.state = 'INITIAL'
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        assert self.state == 'INITIAL', self.state
        self.state = 'CONNECTED'

    def connection_lost(self, exc):
        assert self.state == 'CONNECTED', self.state
        self.state = 'CLOSED'


class EventLoopTestsMixin:

    def setUp(self):
        super().setUp()
        self.event_loop = self.create_event_loop()
        events.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()
        gc.collect()
        super().tearDown()

    @contextlib.contextmanager
    def run_test_server(self, *, use_ssl=False):

        class SilentWSGIRequestHandler(WSGIRequestHandler):
            def get_stderr(self):
                return io.StringIO()

            def log_message(self, format, *args):
                pass

        class SilentWSGIServer(WSGIServer):
            def handle_error(self, request, client_address):
                pass

        class SSLWSGIServer(SilentWSGIServer):
            def finish_request(self, request, client_address):
                here = os.path.dirname(__file__)
                keyfile = os.path.join(here, 'sample.key')
                certfile = os.path.join(here, 'sample.crt')
                ssock = ssl.wrap_socket(request,
                                        keyfile=keyfile,
                                        certfile=certfile,
                                        server_side=True)
                try:
                    self.RequestHandlerClass(ssock, client_address, self)
                    ssock.close()
                except OSError:
                    # maybe socket has been closed by peer
                    pass

        def app(environ, start_response):
            status = '302 Found'
            headers = [('Content-type', 'text/plain')]
            start_response(status, headers)
            return [b'Test message']

        # Run the test WSGI server in a separate thread in order not to
        # interfere with event handling in the main thread
        server_class = SSLWSGIServer if use_ssl else SilentWSGIServer
        httpd = make_server('127.0.0.1', 0, app,
                            server_class, SilentWSGIRequestHandler)
        server_thread = threading.Thread(target=httpd.serve_forever)
        server_thread.start()
        try:
            yield httpd
        finally:
            httpd.shutdown()
            server_thread.join()

    def test_run(self):
        self.event_loop.run()  # Returns immediately.

    def test_run_nesting(self):
        err = None

        @tasks.coroutine
        def coro():
            nonlocal err
            yield from []
            try:
                self.event_loop.run_until_complete(
                    tasks.sleep(0.1))
            except Exception as exc:
                err = exc

        self.event_loop.run_until_complete(tasks.Task(coro()))
        self.assertIsInstance(err, RuntimeError)

    def test_run_once_nesting(self):
        err = None

        @tasks.coroutine
        def coro():
            nonlocal err
            yield from []
            tasks.sleep(0.1)
            try:
                self.event_loop.run_once()
            except Exception as exc:
                err = exc

        self.event_loop.run_until_complete(tasks.Task(coro()))
        self.assertIsInstance(err, RuntimeError)

    def test_call_later(self):
        results = []

        def callback(arg):
            results.append(arg)

        self.event_loop.call_later(0.1, callback, 'hello world')
        t0 = time.monotonic()
        self.event_loop.run()
        t1 = time.monotonic()
        self.assertEqual(results, ['hello world'])
        self.assertTrue(t1-t0 >= 0.09)

    def test_call_repeatedly(self):
        results = []

        def callback(arg):
            results.append(arg)

        self.event_loop.call_repeatedly(0.03, callback, 'ho')
        self.event_loop.call_later(0.1, self.event_loop.stop)
        self.event_loop.run()
        self.assertEqual(results, ['ho', 'ho', 'ho'])

    def test_call_soon(self):
        results = []

        def callback(arg1, arg2):
            results.append((arg1, arg2))

        self.event_loop.call_soon(callback, 'hello', 'world')
        self.event_loop.run()
        self.assertEqual(results, [('hello', 'world')])

    def test_call_soon_with_handle(self):
        results = []

        def callback():
            results.append('yeah')

        handle = events.Handle(callback, ())
        self.assertIs(self.event_loop.call_soon(handle), handle)
        self.event_loop.run()
        self.assertEqual(results, ['yeah'])

    def test_call_soon_threadsafe(self):
        results = []

        def callback(arg):
            results.append(arg)

        def run():
            self.event_loop.call_soon_threadsafe(callback, 'hello')

        t = threading.Thread(target=run)
        self.event_loop.call_later(0.1, callback, 'world')
        t0 = time.monotonic()
        t.start()
        self.event_loop.run()
        t1 = time.monotonic()
        t.join()
        self.assertEqual(results, ['hello', 'world'])
        self.assertTrue(t1-t0 >= 0.09)

    def test_call_soon_threadsafe_same_thread(self):
        results = []

        def callback(arg):
            results.append(arg)

        self.event_loop.call_later(0.1, callback, 'world')
        self.event_loop.call_soon_threadsafe(callback, 'hello')
        self.event_loop.run()
        self.assertEqual(results, ['hello', 'world'])

    def test_call_soon_threadsafe_with_handle(self):
        results = []

        def callback(arg):
            results.append(arg)

        handle = events.Handle(callback, ('hello',))

        def run():
            self.assertIs(
                self.event_loop.call_soon_threadsafe(handle), handle)

        t = threading.Thread(target=run)
        self.event_loop.call_later(0.1, callback, 'world')

        t0 = time.monotonic()
        t.start()
        self.event_loop.run()
        t1 = time.monotonic()
        t.join()
        self.assertEqual(results, ['hello', 'world'])
        self.assertTrue(t1-t0 >= 0.09)

    def test_wrap_future(self):
        def run(arg):
            time.sleep(0.1)
            return arg
        ex = concurrent.futures.ThreadPoolExecutor(1)
        f1 = ex.submit(run, 'oi')
        f2 = self.event_loop.wrap_future(f1)
        res = self.event_loop.run_until_complete(f2)
        self.assertEqual(res, 'oi')

    def test_run_in_executor(self):
        def run(arg):
            time.sleep(0.1)
            return arg
        f2 = self.event_loop.run_in_executor(None, run, 'yo')
        res = self.event_loop.run_until_complete(f2)
        self.assertEqual(res, 'yo')

    def test_run_in_executor_with_handle(self):
        def run(arg):
            time.sleep(0.1)
            return arg
        handle = events.Handle(run, ('yo',))
        f2 = self.event_loop.run_in_executor(None, handle)
        res = self.event_loop.run_until_complete(f2)
        self.assertEqual(res, 'yo')

    def test_reader_callback(self):
        r, w = test_utils.socketpair()
        bytes_read = []

        def reader():
            try:
                data = r.recv(1024)
            except BlockingIOError:
                # Spurious readiness notifications are possible
                # at least on Linux -- see man select.
                return
            if data:
                bytes_read.append(data)
            else:
                self.assertTrue(self.event_loop.remove_reader(r.fileno()))
                r.close()

        self.event_loop.add_reader(r.fileno(), reader)
        self.event_loop.call_later(0.05, w.send, b'abc')
        self.event_loop.call_later(0.1, w.send, b'def')
        self.event_loop.call_later(0.15, w.close)
        self.event_loop.run()
        self.assertEqual(b''.join(bytes_read), b'abcdef')

    def test_reader_callback_with_handle(self):
        r, w = test_utils.socketpair()
        bytes_read = []

        def reader():
            try:
                data = r.recv(1024)
            except BlockingIOError:
                # Spurious readiness notifications are possible
                # at least on Linux -- see man select.
                return
            if data:
                bytes_read.append(data)
            else:
                self.assertTrue(self.event_loop.remove_reader(r.fileno()))
                r.close()

        handle = events.Handle(reader, ())
        self.assertIs(handle, self.event_loop.add_reader(r.fileno(), handle))

        self.event_loop.call_later(0.05, w.send, b'abc')
        self.event_loop.call_later(0.1, w.send, b'def')
        self.event_loop.call_later(0.15, w.close)
        self.event_loop.run()
        self.assertEqual(b''.join(bytes_read), b'abcdef')

    def test_reader_callback_cancel(self):
        r, w = test_utils.socketpair()
        bytes_read = []

        def reader():
            try:
                data = r.recv(1024)
            except BlockingIOError:
                return
            if data:
                bytes_read.append(data)
            if sum(len(b) for b in bytes_read) >= 6:
                handle.cancel()
            if not data:
                r.close()

        handle = self.event_loop.add_reader(r.fileno(), reader)
        self.event_loop.call_later(0.05, w.send, b'abc')
        self.event_loop.call_later(0.1, w.send, b'def')
        self.event_loop.call_later(0.15, w.close)
        self.event_loop.run()
        self.assertEqual(b''.join(bytes_read), b'abcdef')

    def test_writer_callback(self):
        r, w = test_utils.socketpair()
        w.setblocking(False)
        self.event_loop.add_writer(w.fileno(), w.send, b'x'*(256*1024))

        def remove_writer():
            self.assertTrue(self.event_loop.remove_writer(w.fileno()))

        self.event_loop.call_later(0.1, remove_writer)
        self.event_loop.run()
        w.close()
        data = r.recv(256*1024)
        r.close()
        self.assertTrue(len(data) >= 200)

    def test_writer_callback_with_handle(self):
        r, w = test_utils.socketpair()
        w.setblocking(False)
        handle = events.Handle(w.send, (b'x'*(256*1024),))
        self.assertIs(self.event_loop.add_writer(w.fileno(), handle), handle)

        def remove_writer():
            self.assertTrue(self.event_loop.remove_writer(w.fileno()))

        self.event_loop.call_later(0.1, remove_writer)
        self.event_loop.run()
        w.close()
        data = r.recv(256*1024)
        r.close()
        self.assertTrue(len(data) >= 200)

    def test_writer_callback_cancel(self):
        r, w = test_utils.socketpair()
        w.setblocking(False)

        def sender():
            w.send(b'x'*256)
            handle.cancel()

        handle = self.event_loop.add_writer(w.fileno(), sender)
        self.event_loop.run()
        w.close()
        data = r.recv(1024)
        r.close()
        self.assertTrue(data == b'x'*256)

    def test_sock_client_ops(self):
        with self.run_test_server() as httpd:
            sock = socket.socket()
            sock.setblocking(False)
            address = httpd.socket.getsockname()
            self.event_loop.run_until_complete(
                self.event_loop.sock_connect(sock, address))
            self.event_loop.run_until_complete(
                self.event_loop.sock_sendall(sock, b'GET / HTTP/1.0\r\n\r\n'))
            data = self.event_loop.run_until_complete(
                self.event_loop.sock_recv(sock, 1024))
            sock.close()

        self.assertTrue(re.match(rb'HTTP/1.0 302 Found', data), data)

    def test_sock_client_fail(self):
        # Make sure that we will get an unused port
        address = None
        try:
            s = socket.socket()
            s.bind(('127.0.0.1', 0))
            address = s.getsockname()
        finally:
            s.close()

        sock = socket.socket()
        sock.setblocking(False)
        with self.assertRaises(ConnectionRefusedError):
            self.event_loop.run_until_complete(
                self.event_loop.sock_connect(sock, address))
        sock.close()

    def test_sock_accept(self):
        listener = socket.socket()
        listener.setblocking(False)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        client = socket.socket()
        client.connect(listener.getsockname())

        f = self.event_loop.sock_accept(listener)
        conn, addr = self.event_loop.run_until_complete(f)
        self.assertEqual(conn.gettimeout(), 0)
        self.assertEqual(addr, client.getsockname())
        self.assertEqual(client.getpeername(), listener.getsockname())
        client.close()
        conn.close()
        listener.close()

    @unittest.skipUnless(hasattr(signal, 'SIGKILL'), 'No SIGKILL')
    def test_add_signal_handler(self):
        caught = 0

        def my_handler():
            nonlocal caught
            caught += 1

        # Check error behavior first.
        self.assertRaises(
            TypeError, self.event_loop.add_signal_handler, 'boom', my_handler)
        self.assertRaises(
            TypeError, self.event_loop.remove_signal_handler, 'boom')
        self.assertRaises(
            ValueError, self.event_loop.add_signal_handler, signal.NSIG+1,
            my_handler)
        self.assertRaises(
            ValueError, self.event_loop.remove_signal_handler, signal.NSIG+1)
        self.assertRaises(
            ValueError, self.event_loop.add_signal_handler, 0, my_handler)
        self.assertRaises(
            ValueError, self.event_loop.remove_signal_handler, 0)
        self.assertRaises(
            ValueError, self.event_loop.add_signal_handler, -1, my_handler)
        self.assertRaises(
            ValueError, self.event_loop.remove_signal_handler, -1)
        self.assertRaises(
            RuntimeError, self.event_loop.add_signal_handler, signal.SIGKILL,
            my_handler)
        # Removing SIGKILL doesn't raise, since we don't call signal().
        self.assertFalse(self.event_loop.remove_signal_handler(signal.SIGKILL))
        # Now set a handler and handle it.
        self.event_loop.add_signal_handler(signal.SIGINT, my_handler)
        self.event_loop.run_once()
        os.kill(os.getpid(), signal.SIGINT)
        self.event_loop.run_once()
        self.assertEqual(caught, 1)
        # Removing it should restore the default handler.
        self.assertTrue(self.event_loop.remove_signal_handler(signal.SIGINT))
        self.assertEqual(signal.getsignal(signal.SIGINT),
                         signal.default_int_handler)
        # Removing again returns False.
        self.assertFalse(self.event_loop.remove_signal_handler(signal.SIGINT))

    @unittest.skipIf(sys.platform == 'win32', 'Unix only')
    def test_cancel_signal_handler(self):
        # Cancelling the handler should remove it (eventually).
        caught = 0

        def my_handler():
            nonlocal caught
            caught += 1

        handle = self.event_loop.add_signal_handler(signal.SIGINT, my_handler)
        handle.cancel()
        os.kill(os.getpid(), signal.SIGINT)
        self.event_loop.run_once()
        self.assertEqual(caught, 0)

    @unittest.skipUnless(hasattr(signal, 'SIGALRM'), 'No SIGALRM')
    def test_signal_handling_while_selecting(self):
        # Test with a signal actually arriving during a select() call.
        caught = 0

        def my_handler():
            nonlocal caught
            caught += 1

        self.event_loop.add_signal_handler(
            signal.SIGALRM, my_handler)

        signal.setitimer(signal.ITIMER_REAL, 0.1, 0)  # Send SIGALRM once.
        self.event_loop.call_later(0.15, self.event_loop.stop)
        self.event_loop.run_forever()
        self.assertEqual(caught, 1)

    @unittest.skipUnless(hasattr(signal, 'SIGALRM'), 'No SIGALRM')
    def test_signal_handling_args(self):
        some_args = (42,)
        caught = 0

        def my_handler(*args):
            nonlocal caught
            caught += 1
            self.assertEqual(args, some_args)

        self.event_loop.add_signal_handler(
            signal.SIGALRM, my_handler, *some_args)

        signal.setitimer(signal.ITIMER_REAL, 0.1, 0)  # Send SIGALRM once.
        self.event_loop.call_later(0.15, self.event_loop.stop)
        self.event_loop.run_forever()
        self.assertEqual(caught, 1)

    def test_create_connection(self):
        with self.run_test_server() as httpd:
            host, port = httpd.socket.getsockname()
            f = tasks.Task(
                self.event_loop.create_connection(MyProto, host, port))
            tr, pr = self.event_loop.run_until_complete(f)
            self.assertTrue(isinstance(tr, transports.Transport))
            self.assertTrue(isinstance(pr, protocols.Protocol))
            self.event_loop.run()
            self.assertTrue(pr.nbytes > 0)

    def test_create_connection_sock(self):
        with self.run_test_server() as httpd:
            host, port = httpd.socket.getsockname()
            f = tasks.Task(
                self.event_loop.create_connection(MyProto, host, port))
            tr, pr = self.event_loop.run_until_complete(f)
            self.assertTrue(isinstance(tr, transports.Transport))
            self.assertTrue(isinstance(pr, protocols.Protocol))
            self.event_loop.run()
            self.assertTrue(pr.nbytes > 0)

    @unittest.skipIf(ssl is None, 'No ssl module')
    def test_create_ssl_connection(self):
        with self.run_test_server(use_ssl=True) as httpsd:
            host, port = httpsd.socket.getsockname()
            f = self.event_loop.create_connection(
                MyProto, host, port, ssl=True)
            tr, pr = self.event_loop.run_until_complete(f)
            self.assertTrue(isinstance(tr, transports.Transport))
            self.assertTrue(isinstance(pr, protocols.Protocol))
            self.assertTrue('ssl' in tr.__class__.__name__.lower())
            self.assertTrue(
                hasattr(tr.get_extra_info('socket'), 'getsockname'))
            self.event_loop.run()
            self.assertTrue(pr.nbytes > 0)

    def test_create_connection_host_port_sock(self):
        self.suppress_log_errors()
        coro = self.event_loop.create_connection(
            MyProto, 'xkcd.com', 80, sock=object())
        self.assertRaises(ValueError, self.event_loop.run_until_complete, coro)

    def test_create_connection_no_host_port_sock(self):
        self.suppress_log_errors()
        coro = self.event_loop.create_connection(MyProto)
        self.assertRaises(ValueError, self.event_loop.run_until_complete, coro)

    def test_create_connection_no_getaddrinfo(self):
        self.suppress_log_errors()
        getaddrinfo = self.event_loop.getaddrinfo = unittest.mock.Mock()
        getaddrinfo.return_value = []

        coro = self.event_loop.create_connection(MyProto, 'xkcd.com', 80)
        self.assertRaises(
            socket.error, self.event_loop.run_until_complete, coro)

    def test_create_connection_connect_err(self):
        self.suppress_log_errors()
        self.event_loop.sock_connect = unittest.mock.Mock()
        self.event_loop.sock_connect.side_effect = socket.error

        coro = self.event_loop.create_connection(MyProto, 'xkcd.com', 80)
        self.assertRaises(
            socket.error, self.event_loop.run_until_complete, coro)

    def test_create_connection_mutiple_errors(self):
        self.suppress_log_errors()

        def getaddrinfo(*args, **kw):
            yield from []
            return [(2, 1, 6, '', ('107.6.106.82', 80)),
                    (2, 1, 6, '', ('107.6.106.82', 80))]
        self.event_loop.getaddrinfo = getaddrinfo
        self.event_loop.sock_connect = unittest.mock.Mock()
        self.event_loop.sock_connect.side_effect = socket.error

        coro = self.event_loop.create_connection(MyProto, 'xkcd.com', 80)
        self.assertRaises(
            socket.error, self.event_loop.run_until_complete, coro)

    def test_start_serving(self):
        proto = None

        def factory():
            nonlocal proto
            proto = MyProto()
            return proto

        f = self.event_loop.start_serving(factory, '0.0.0.0', 0)
        sock = self.event_loop.run_until_complete(f)
        host, port = sock.getsockname()
        self.assertEqual(host, '0.0.0.0')
        client = socket.socket()
        client.connect(('127.0.0.1', port))
        client.send(b'xxx')
        self.event_loop.run_once()
        self.assertIsInstance(proto, MyProto)
        self.assertEqual('INITIAL', proto.state)
        self.event_loop.run_once()
        self.assertEqual('CONNECTED', proto.state)
        self.assertEqual(0, proto.nbytes)
        self.event_loop.run_once()
        self.assertEqual(3, proto.nbytes)

        # extra info is available
        self.assertIsNotNone(proto.transport.get_extra_info('socket'))
        conn = proto.transport.get_extra_info('socket')
        self.assertTrue(hasattr(conn, 'getsockname'))
        self.assertEqual(
            '127.0.0.1', proto.transport.get_extra_info('addr')[0])

        # close connection
        proto.transport.close()

        self.event_loop.run_once()
        self.assertEqual('CLOSED', proto.state)

        # the client socket must be closed after to avoid ECONNRESET upon
        # recv()/send() on the serving socket
        client.close()

    def test_start_serving_sock(self):
        sock_ob = socket.socket(type=socket.SOCK_STREAM)
        sock_ob.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock_ob.bind(('0.0.0.0', 0))

        f = self.event_loop.start_serving(MyProto, sock=sock_ob)
        sock = self.event_loop.run_until_complete(f)
        self.assertIs(sock, sock_ob)

        host, port = sock.getsockname()
        self.assertEqual(host, '0.0.0.0')
        client = socket.socket()
        client.connect(('127.0.0.1', port))
        client.send(b'xxx')
        self.event_loop.run_once()  # This is quite mysterious, but necessary.
        self.event_loop.run_once()
        self.event_loop.run_once()
        sock.close()
        client.close()

    def test_start_serving_host_port_sock(self):
        self.suppress_log_errors()
        fut = self.event_loop.start_serving(
            MyProto, '0.0.0.0', 0, sock=object())
        self.assertRaises(ValueError, self.event_loop.run_until_complete, fut)

    def test_start_serving_no_host_port_sock(self):
        self.suppress_log_errors()
        fut = self.event_loop.start_serving(MyProto)
        self.assertRaises(ValueError, self.event_loop.run_until_complete, fut)

    def test_start_serving_no_getaddrinfo(self):
        self.suppress_log_errors()
        getaddrinfo = self.event_loop.getaddrinfo = unittest.mock.Mock()
        getaddrinfo.return_value = []

        f = self.event_loop.start_serving(MyProto, '0.0.0.0', 0)
        self.assertRaises(
            socket.error, self.event_loop.run_until_complete, f)

    @unittest.mock.patch('tulip.base_events.socket')
    def test_start_serving_cant_bind(self, m_socket):
        self.suppress_log_errors()

        class Err(socket.error):
            pass

        m_socket.error = socket.error
        m_socket.getaddrinfo.return_value = [
            (2, 1, 6, '', ('127.0.0.1', 10100))]
        m_sock = m_socket.socket.return_value = unittest.mock.Mock()
        m_sock.setsockopt.side_effect = Err

        fut = self.event_loop.start_serving(MyProto, '0.0.0.0', 0)
        self.assertRaises(Err, self.event_loop.run_until_complete, fut)
        self.assertTrue(m_sock.close.called)

    def test_create_datagram_connection(self):
        server = None

        def factory():
            nonlocal server
            server = TestMyDatagramProto()
            return server

        class TestMyDatagramProto(MyDatagramProto):
            def datagram_received(self, data, addr):
                super().datagram_received(data, addr)
                self.transport.sendto(b'resp:'+data, addr)

        f = self.event_loop.start_serving_datagram(factory, '127.0.0.1', 0)
        sock = self.event_loop.run_until_complete(f)
        host, port = sock.getsockname()

        coro = self.event_loop.create_datagram_connection(
            MyDatagramProto, host, port)
        transport, protocol = self.event_loop.run_until_complete(coro)

        self.assertEqual('INITIALIZED', protocol.state)
        transport.sendto(b'xxx')
        self.event_loop.run_once()
        self.assertEqual(0, server.nbytes)
        self.event_loop.run_once()
        self.assertEqual(3, server.nbytes)
        self.event_loop.run_once()

        # received
        self.event_loop.run_once()
        self.assertEqual(8, protocol.nbytes)

        # extra info is available
        self.assertIsNotNone(transport.get_extra_info('socket'))
        conn = transport.get_extra_info('socket')
        self.assertTrue(hasattr(conn, 'getsockname'))

        # close connection
        transport.close()

        self.event_loop.run_once()
        self.assertEqual('CLOSED', protocol.state)

        server.transport.close()

    def test_create_datagram_connection_no_connection(self):
        server = None

        def factory():
            nonlocal server
            server = TestMyDatagramProto()
            return server

        class TestMyDatagramProto(MyDatagramProto):
            def datagram_received(self, data, addr):
                super().datagram_received(data, addr)
                self.transport.sendto(b'resp:'+data, addr)

        f = self.event_loop.start_serving_datagram(factory, '127.0.0.1', 0)
        sock = self.event_loop.run_until_complete(f)
        host, port = sock.getsockname()

        coro = self.event_loop.create_datagram_connection(MyDatagramProto)
        transport, protocol = self.event_loop.run_until_complete(coro)

        self.assertEqual('INITIALIZED', protocol.state)
        transport.sendto(b'xxx', (host, port))
        self.event_loop.run_once()
        self.assertEqual(0, server.nbytes)
        self.event_loop.run_once()
        self.assertEqual(3, server.nbytes)
        self.event_loop.run_once()

        # received
        self.event_loop.run_once()
        self.assertEqual(8, protocol.nbytes)

        # extra info is available
        self.assertIsNotNone(transport.get_extra_info('socket'))
        conn = transport.get_extra_info('socket')
        self.assertTrue(hasattr(conn, 'getsockname'))

        # close connection
        transport.close()

        self.event_loop.run_once()
        self.assertEqual('CLOSED', protocol.state)

        server.transport.close()

    def test_create_datagram_connection_no_getaddrinfo(self):
        self.suppress_log_errors()
        getaddrinfo = self.event_loop.getaddrinfo = unittest.mock.Mock()
        getaddrinfo.return_value = []

        coro = self.event_loop.create_datagram_connection(
            protocols.DatagramProtocol, 'xkcd.com', 80)
        self.assertRaises(
            socket.error, self.event_loop.run_until_complete, coro)

    def test_create_datagram_connection_connect_err(self):
        self.suppress_log_errors()
        self.event_loop.sock_connect = unittest.mock.Mock()
        self.event_loop.sock_connect.side_effect = socket.error

        coro = self.event_loop.create_datagram_connection(
            protocols.DatagramProtocol, 'xkcd.com', 80)
        self.assertRaises(
            socket.error, self.event_loop.run_until_complete, coro)

    @unittest.mock.patch('tulip.base_events.socket')
    def test_create_datagram_connection_sockopt_err(self, m_socket):
        self.suppress_log_errors()

        m_socket.error = socket.error
        m_socket.socket.return_value.setsockopt.side_effect = socket.error

        coro = self.event_loop.create_datagram_connection(
            protocols.DatagramProtocol)
        self.assertRaises(
            socket.error, self.event_loop.run_until_complete, coro)
        self.assertTrue(
            m_socket.socket.return_value.close.called)

    def test_start_serving_datagram(self):
        class TestMyDatagramProto(MyDatagramProto):
            def datagram_received(self, data, addr):
                super().datagram_received(data, addr)
                self.transport.sendto(b'resp:'+data, addr)

        proto = None

        def factory():
            nonlocal proto
            proto = TestMyDatagramProto()
            return proto

        f = self.event_loop.start_serving_datagram(factory, '127.0.0.1', 0)
        sock = self.event_loop.run_until_complete(f)
        self.assertEqual('INITIALIZED', proto.state)

        host, port = sock.getsockname()
        self.assertEqual(host, '127.0.0.1')
        client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        client.sendto(b'xxx', ('127.0.0.1', port))
        self.event_loop.run_once()
        self.assertEqual(0, proto.nbytes)
        self.event_loop.run_once()
        self.assertEqual(3, proto.nbytes)

        data, server = client.recvfrom(4096)
        self.assertEqual(b'resp:xxx', data)

        # extra info is available
        self.assertIsNotNone(proto.transport.get_extra_info('socket'))
        conn = proto.transport.get_extra_info('socket')
        self.assertTrue(hasattr(conn, 'getsockname'))
        self.assertEqual(
            '127.0.0.1', proto.transport.get_extra_info('addr')[0])

        # close connection
        proto.transport.close()

        self.event_loop.run_once()
        self.assertEqual('CLOSED', proto.state)

        client.close()

    def test_start_serving_datagram_no_getaddrinfoc(self):
        self.suppress_log_errors()
        getaddrinfo = self.event_loop.getaddrinfo = unittest.mock.Mock()
        getaddrinfo.return_value = []

        f = self.event_loop.start_serving_datagram(
            MyDatagramProto, '0.0.0.0', 0)

        self.assertRaises(
            socket.error, self.event_loop.run_until_complete, f)

    @unittest.mock.patch('tulip.base_events.socket')
    def test_start_serving_datagram_cant_bind(self, m_socket):
        self.suppress_log_errors()

        class Err(socket.error):
            pass

        m_socket.error = socket.error
        m_socket.getaddrinfo.return_value = [
            (2, 1, 6, '', ('127.0.0.1', 10100))]
        m_sock = m_socket.socket.return_value = unittest.mock.Mock()
        m_sock.setsockopt.side_effect = Err

        fut = self.event_loop.start_serving_datagram(
            MyDatagramProto, '0.0.0.0', 0)
        self.assertRaises(Err, self.event_loop.run_until_complete, fut)
        self.assertTrue(m_sock.close.called)

    def test_accept_connection_retry(self):
        class Err(socket.error):
            errno = errno.EAGAIN

        sock = unittest.mock.Mock()
        sock.accept.side_effect = Err

        self.event_loop._accept_connection(MyProto, sock)
        self.assertFalse(sock.close.called)

    def test_accept_connection_exception(self):
        self.suppress_log_errors()

        sock = unittest.mock.Mock()
        sock.accept.side_effect = socket.error

        self.event_loop._accept_connection(MyProto, sock)
        self.assertTrue(sock.close.called)

    def test_internal_fds(self):
        event_loop = self.create_event_loop()
        if not isinstance(event_loop, selector_events.BaseSelectorEventLoop):
            return

        self.assertEqual(1, event_loop._internal_fds)
        event_loop.close()
        self.assertEqual(0, event_loop._internal_fds)
        self.assertIsNone(event_loop._csock)
        self.assertIsNone(event_loop._ssock)

    @unittest.skipUnless(sys.platform != 'win32',
                         "Don't support pipes for Windows")
    def test_read_pipe(self):
        proto = None

        def factory():
            nonlocal proto
            proto = MyReadPipeProto()
            return proto

        rpipe, wpipe = os.pipe()
        pipeobj = io.open(rpipe, 'rb', 1024)

        @tasks.task
        def connect():
            t, p = yield from self.event_loop.connect_read_pipe(factory,
                                                                pipeobj)
            self.assertIs(p, proto)
            self.assertIs(t, proto.transport)
            self.assertEqual(['INITIAL', 'CONNECTED'], proto.state)
            self.assertEqual(0, proto.nbytes)

        self.event_loop.run_until_complete(connect())

        os.write(wpipe, b'1')
        self.event_loop.run_once()
        self.assertEqual(1, proto.nbytes)

        os.write(wpipe, b'2345')
        self.event_loop.run_once()
        self.assertEqual(['INITIAL', 'CONNECTED'], proto.state)
        self.assertEqual(5, proto.nbytes)

        os.close(wpipe)
        self.event_loop.run_once()
        self.assertEqual(['INITIAL', 'CONNECTED', 'EOF', 'CLOSED'], proto.state)
        # extra info is available
        self.assertIsNotNone(proto.transport.get_extra_info('pipe'))

    @unittest.skipUnless(sys.platform != 'win32',
                         "Don't support pipes for Windows")
    def test_write_pipe(self):
        proto = None
        transport = None

        def factory():
            nonlocal proto
            proto = MyWritePipeProto()
            return proto

        rpipe, wpipe = os.pipe()
        pipeobj = io.open(wpipe, 'wb', 1024)

        @tasks.task
        def connect():
            nonlocal transport
            t, p = yield from self.event_loop.connect_write_pipe(factory,
                                                                 pipeobj)
            self.assertIs(p, proto)
            self.assertIs(t, proto.transport)
            self.assertEqual('CONNECTED', proto.state)
            transport = t

        self.event_loop.run_until_complete(connect())

        transport.write(b'1')
        self.event_loop.run_once()
        data = os.read(rpipe, 1024)
        self.assertEqual(b'1', data)

        transport.write(b'2345')
        self.event_loop.run_once()
        data = os.read(rpipe, 1024)
        self.assertEqual(b'2345', data)
        self.assertEqual('CONNECTED', proto.state)

        os.close(rpipe)

        # extra info is available
        self.assertIsNotNone(proto.transport.get_extra_info('pipe'))

        # close connection
        proto.transport.close()
        self.event_loop.run_once()
        self.assertEqual('CLOSED', proto.state)


if sys.platform == 'win32':
    from tulip import windows_events

    class SelectEventLoopTests(EventLoopTestsMixin,
                               test_utils.LogTrackingTestCase):
        def create_event_loop(self):
            return windows_events.SelectorEventLoop()

    class ProactorEventLoopTests(EventLoopTestsMixin,
                                 test_utils.LogTrackingTestCase):
        def create_event_loop(self):
            return windows_events.ProactorEventLoop()
        def test_create_ssl_connection(self):
            raise unittest.SkipTest("IocpEventLoop imcompatible with SSL")
        def test_reader_callback(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_reader()")
        def test_reader_callback_cancel(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_reader()")
        def test_reader_callback_with_handle(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_reader()")
        def test_writer_callback(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_writer()")
        def test_writer_callback_cancel(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_writer()")
        def test_writer_callback_with_handle(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_writer()")
        def test_accept_connection_retry(self):
            raise unittest.SkipTest(
                "IocpEventLoop does not have _accept_connection()")
        def test_accept_connection_exception(self):
            raise unittest.SkipTest(
                "IocpEventLoop does not have _accept_connection()")
        def test_create_datagram_connection(self):
            raise unittest.SkipTest(
                "IocpEventLoop does not have create_datagram_connection()")
        def test_create_datagram_connection_no_connection(self):
            raise unittest.SkipTest(
                "IocpEventLoop does not have "
                "create_datagram_connection_no_connection()")
        def test_start_serving_datagram(self):
            raise unittest.SkipTest(
                "IocpEventLoop does not have start_serving_datagram()")
        def test_start_serving_datagram_no_getaddrinfoc(self):
            raise unittest.SkipTest(
                "IocpEventLoop does not have start_serving_datagram()")
        def test_start_serving_datagram_cant_bind(self):
            raise unittest.SkipTest(
                "IocpEventLoop does not have start_serving_udp()")

else:
    from tulip import selectors
    from tulip import unix_events

    if hasattr(selectors, 'KqueueSelector'):
        class KqueueEventLoopTests(EventLoopTestsMixin,
                                   test_utils.LogTrackingTestCase):
            def create_event_loop(self):
                return unix_events.SelectorEventLoop(
                    selectors.KqueueSelector())

    if hasattr(selectors, 'EpollSelector'):
        class EPollEventLoopTests(EventLoopTestsMixin,
                                  test_utils.LogTrackingTestCase):
            def create_event_loop(self):
                return unix_events.SelectorEventLoop(selectors.EpollSelector())

    if hasattr(selectors, 'PollSelector'):
        class PollEventLoopTests(EventLoopTestsMixin,
                                 test_utils.LogTrackingTestCase):
            def create_event_loop(self):
                return unix_events.SelectorEventLoop(selectors.PollSelector())

    # Should always exist.
    class SelectEventLoopTests(EventLoopTestsMixin,
                               test_utils.LogTrackingTestCase):
        def create_event_loop(self):
            return unix_events.SelectorEventLoop(selectors.SelectSelector())


class HandleTests(unittest.TestCase):

    def test_handle(self):
        def callback(*args):
            return args

        args = ()
        h = events.Handle(callback, args)
        self.assertIs(h.callback, callback)
        self.assertIs(h.args, args)
        self.assertFalse(h.cancelled)

        r = repr(h)
        self.assertTrue(r.startswith(
            'Handle('
            '<function HandleTests.test_handle.<locals>.callback'))
        self.assertTrue(r.endswith('())'))

        h.cancel()
        self.assertTrue(h.cancelled)

        r = repr(h)
        self.assertTrue(r.startswith(
            'Handle('
            '<function HandleTests.test_handle.<locals>.callback'))
        self.assertTrue(r.endswith('())<cancelled>'))

    def test_make_handle(self):
        def callback(*args):
            return args
        h1 = events.Handle(callback, ())
        h2 = events.make_handle(h1, ())
        self.assertIs(h1, h2)

        self.assertRaises(
            AssertionError, events.make_handle, h1, (1, 2))


class TimerTests(unittest.TestCase):

    def test_timer(self):
        def callback(*args):
            return args

        args = ()
        when = time.monotonic()
        h = events.Timer(when, callback, args)
        self.assertIs(h.callback, callback)
        self.assertIs(h.args, args)
        self.assertFalse(h.cancelled)

        r = repr(h)
        self.assertTrue(r.endswith('())'))

        h.cancel()
        self.assertTrue(h.cancelled)

        r = repr(h)
        self.assertTrue(r.endswith('())<cancelled>'))

        self.assertRaises(AssertionError, events.Timer, None, callback, args)

    def test_timer_comparison(self):
        def callback(*args):
            return args

        when = time.monotonic()

        h1 = events.Timer(when, callback, ())
        h2 = events.Timer(when, callback, ())
        self.assertFalse(h1 < h2)
        self.assertFalse(h2 < h1)
        self.assertTrue(h1 <= h2)
        self.assertTrue(h2 <= h1)
        self.assertFalse(h1 > h2)
        self.assertFalse(h2 > h1)
        self.assertTrue(h1 >= h2)
        self.assertTrue(h2 >= h1)
        self.assertTrue(h1 == h2)
        self.assertFalse(h1 != h2)

        h2.cancel()
        self.assertFalse(h1 == h2)

        h1 = events.Timer(when, callback, ())
        h2 = events.Timer(when + 10.0, callback, ())
        self.assertTrue(h1 < h2)
        self.assertFalse(h2 < h1)
        self.assertTrue(h1 <= h2)
        self.assertFalse(h2 <= h1)
        self.assertFalse(h1 > h2)
        self.assertTrue(h2 > h1)
        self.assertFalse(h1 >= h2)
        self.assertTrue(h2 >= h1)
        self.assertFalse(h1 == h2)
        self.assertTrue(h1 != h2)

        h3 = events.Handle(callback, ())
        self.assertIs(NotImplemented, h1.__eq__(h3))
        self.assertIs(NotImplemented, h1.__ne__(h3))


class AbstractEventLoopTests(unittest.TestCase):

    def test_not_imlemented(self):
        f = unittest.mock.Mock()
        ev_loop = events.AbstractEventLoop()
        self.assertRaises(
            NotImplementedError, ev_loop.run)
        self.assertRaises(
            NotImplementedError, ev_loop.run_forever)
        self.assertRaises(
            NotImplementedError, ev_loop.run_once)
        self.assertRaises(
            NotImplementedError, ev_loop.run_until_complete, None)
        self.assertRaises(
            NotImplementedError, ev_loop.stop)
        self.assertRaises(
            NotImplementedError, ev_loop.call_later, None, None)
        self.assertRaises(
            NotImplementedError, ev_loop.call_repeatedly, None, None)
        self.assertRaises(
            NotImplementedError, ev_loop.call_soon, None)
        self.assertRaises(
            NotImplementedError, ev_loop.call_soon_threadsafe, None)
        self.assertRaises(
            NotImplementedError, ev_loop.wrap_future, f)
        self.assertRaises(
            NotImplementedError, ev_loop.run_in_executor, f, f)
        self.assertRaises(
            NotImplementedError, ev_loop.getaddrinfo, 'localhost', 8080)
        self.assertRaises(
            NotImplementedError, ev_loop.getnameinfo, ('localhost', 8080))
        self.assertRaises(
            NotImplementedError, ev_loop.create_connection, f)
        self.assertRaises(
            NotImplementedError, ev_loop.start_serving, f)
        self.assertRaises(
            NotImplementedError, ev_loop.create_datagram_connection, f)
        self.assertRaises(
            NotImplementedError, ev_loop.start_serving_datagram,
            f, 'localhost', 8080)
        self.assertRaises(
            NotImplementedError, ev_loop.add_reader, 1, f)
        self.assertRaises(
            NotImplementedError, ev_loop.remove_reader, 1)
        self.assertRaises(
            NotImplementedError, ev_loop.add_writer, 1, f)
        self.assertRaises(
            NotImplementedError, ev_loop.remove_writer, 1)
        self.assertRaises(
            NotImplementedError, ev_loop.sock_recv, f, 10)
        self.assertRaises(
            NotImplementedError, ev_loop.sock_sendall, f, 10)
        self.assertRaises(
            NotImplementedError, ev_loop.sock_connect, f, f)
        self.assertRaises(
            NotImplementedError, ev_loop.sock_accept, f)
        self.assertRaises(
            NotImplementedError, ev_loop.add_signal_handler, 1, f)
        self.assertRaises(
            NotImplementedError, ev_loop.remove_signal_handler, 1)
        self.assertRaises(
            NotImplementedError, ev_loop.remove_signal_handler, 1)
        self.assertRaises(
            NotImplementedError, ev_loop.connect_read_pipe, f,
            unittest.mock.sentinel.pipe)
        self.assertRaises(
            NotImplementedError, ev_loop.connect_write_pipe, f,
            unittest.mock.sentinel.pipe)


class ProtocolsAbsTests(unittest.TestCase):

    def test_empty(self):
        f = unittest.mock.Mock()
        p = protocols.Protocol()
        self.assertIsNone(p.connection_made(f))
        self.assertIsNone(p.connection_lost(f))
        self.assertIsNone(p.data_received(f))
        self.assertIsNone(p.eof_received())

        dp = protocols.DatagramProtocol()
        self.assertIsNone(dp.connection_made(f))
        self.assertIsNone(dp.connection_lost(f))
        self.assertIsNone(dp.connection_refused(f))
        self.assertIsNone(dp.datagram_received(f, f))


class PolicyTests(unittest.TestCase):

    def test_event_loop_policy(self):
        policy = events.EventLoopPolicy()
        self.assertRaises(NotImplementedError, policy.get_event_loop)
        self.assertRaises(NotImplementedError, policy.set_event_loop, object())
        self.assertRaises(NotImplementedError, policy.new_event_loop)

    def test_get_event_loop(self):
        policy = events.DefaultEventLoopPolicy()
        self.assertIsNone(policy._event_loop)

        event_loop = policy.get_event_loop()
        self.assertIsInstance(event_loop, events.AbstractEventLoop)

        self.assertIs(policy._event_loop, event_loop)
        self.assertIs(event_loop, policy.get_event_loop())

    @unittest.mock.patch('tulip.events.threading')
    def test_get_event_loop_thread(self, m_threading):
        m_t = m_threading.current_thread.return_value = unittest.mock.Mock()
        m_t.name = 'Thread 1'

        policy = events.DefaultEventLoopPolicy()
        self.assertIsNone(policy.get_event_loop())

    def test_new_event_loop(self):
        policy = events.DefaultEventLoopPolicy()

        event_loop = policy.new_event_loop()
        self.assertIsInstance(event_loop, events.AbstractEventLoop)

    def test_set_event_loop(self):
        policy = events.DefaultEventLoopPolicy()
        old_event_loop = policy.get_event_loop()

        self.assertRaises(AssertionError, policy.set_event_loop, object())

        event_loop = policy.new_event_loop()
        policy.set_event_loop(event_loop)
        self.assertIs(event_loop, policy.get_event_loop())
        self.assertIsNot(old_event_loop, policy.get_event_loop())

    def test_get_event_loop_policy(self):
        policy = events.get_event_loop_policy()
        self.assertIsInstance(policy, events.EventLoopPolicy)
        self.assertIs(policy, events.get_event_loop_policy())

    def test_set_event_loop_policy(self):
        self.assertRaises(
            AssertionError, events.set_event_loop_policy, object())

        old_policy = events.get_event_loop_policy()

        policy = events.DefaultEventLoopPolicy()
        events.set_event_loop_policy(policy)
        self.assertIs(policy, events.get_event_loop_policy())
        self.assertIsNot(policy, old_policy)


if __name__ == '__main__':
    unittest.main()
