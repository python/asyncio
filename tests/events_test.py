"""Tests for events.py."""

import concurrent.futures
import errno
import gc
import os
import select
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

from tulip import events
from tulip import futures
from tulip import transports
from tulip import protocols
from tulip import selector_events
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


class EventLoopTestsMixin:

    def setUp(self):
        super().setUp()
        self.event_loop = self.create_event_loop()
        events.set_event_loop(self.event_loop)

    def tearDown(self):
        self.event_loop.close()
        gc.collect()
        super().tearDown()

    def test_run(self):
        self.event_loop.run()  # Returns immediately.

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

    def test_call_soon_with_handler(self):
        results = []
        def callback():
            results.append('yeah')
        handler = events.Handler(callback, ())
        self.assertIs(self.event_loop.call_soon(handler), handler)
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

    def test_call_soon_threadsafe_with_handler(self):
        results = []
        def callback(arg):
            results.append(arg)

        handler = events.Handler(callback, ('hello',))
        def run():
            self.assertIs(self.event_loop.call_soon_threadsafe(handler),handler)

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

    def test_run_in_executor_with_handler(self):
        def run(arg):
            time.sleep(0.1)
            return arg
        handler = events.Handler(run, ('yo',))
        f2 = self.event_loop.run_in_executor(None, handler)
        res = self.event_loop.run_until_complete(f2)
        self.assertEqual(res, 'yo')

    def test_reader_callback(self):
        r, w = self.event_loop._socketpair()
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

    def test_reader_callback_with_handler(self):
        r, w = self.event_loop._socketpair()
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

        handler = events.Handler(reader, ())
        self.assertIs(handler, self.event_loop.add_reader(r.fileno(), handler))

        self.event_loop.call_later(0.05, w.send, b'abc')
        self.event_loop.call_later(0.1, w.send, b'def')
        self.event_loop.call_later(0.15, w.close)
        self.event_loop.run()
        self.assertEqual(b''.join(bytes_read), b'abcdef')

    def test_reader_callback_cancel(self):
        r, w = self.event_loop._socketpair()
        bytes_read = []
        def reader():
            try:
                data = r.recv(1024)
            except BlockingIOError:
                return
            if data:
                bytes_read.append(data)
            if sum(len(b) for b in bytes_read) >= 6:
                handler.cancel()
            if not data:
                r.close()
        handler = self.event_loop.add_reader(r.fileno(), reader)
        self.event_loop.call_later(0.05, w.send, b'abc')
        self.event_loop.call_later(0.1, w.send, b'def')
        self.event_loop.call_later(0.15, w.close)
        self.event_loop.run()
        self.assertEqual(b''.join(bytes_read), b'abcdef')

    def test_writer_callback(self):
        r, w = self.event_loop._socketpair()
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

    def test_writer_callback_with_handler(self):
        r, w = self.event_loop._socketpair()
        w.setblocking(False)
        handler = events.Handler(w.send, (b'x'*(256*1024),))
        self.assertIs(self.event_loop.add_writer(w.fileno(), handler), handler)
        def remove_writer():
            self.assertTrue(self.event_loop.remove_writer(w.fileno()))
        self.event_loop.call_later(0.1, remove_writer)
        self.event_loop.run()
        w.close()
        data = r.recv(256*1024)
        r.close()
        self.assertTrue(len(data) >= 200)

    def test_writer_callback_cancel(self):
        r, w = self.event_loop._socketpair()
        w.setblocking(False)
        def sender():
            w.send(b'x'*256)
            handler.cancel()
        handler = self.event_loop.add_writer(w.fileno(), sender)
        self.event_loop.run()
        w.close()
        data = r.recv(1024)
        r.close()
        self.assertTrue(data == b'x'*256)

    def test_sock_client_ops(self):
        sock = socket.socket()
        sock.setblocking(False)
        # TODO: This depends on python.org behavior!
        address = socket.getaddrinfo('python.org', 80, socket.AF_INET)[0][4]
        self.event_loop.run_until_complete(
            self.event_loop.sock_connect(sock, address))
        self.event_loop.run_until_complete(
            self.event_loop.sock_sendall(sock, b'GET / HTTP/1.0\r\n\r\n'))
        data = self.event_loop.run_until_complete(
            self.event_loop.sock_recv(sock, 1024))
        sock.close()
        self.assertTrue(data.startswith(b'HTTP/1.1 302 Found\r\n'))

    def test_sock_client_fail(self):
        sock = socket.socket()
        sock.setblocking(False)
        # TODO: This depends on python.org behavior!
        address = socket.getaddrinfo('python.org', 12345, socket.AF_INET)[0][4]
        with self.assertRaises(ConnectionRefusedError):
            self.event_loop.run_until_complete(
                self.event_loop.sock_connect(sock, address))
        sock.close()

    def test_sock_accept(self):
        el = events.get_event_loop()
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

        handler = self.event_loop.add_signal_handler(signal.SIGINT, my_handler)
        handler.cancel()
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

        handler = self.event_loop.add_signal_handler(signal.SIGALRM, my_handler)
        signal.setitimer(signal.ITIMER_REAL, 0.1, 0)  # Send SIGALRM once.
        self.event_loop.call_later(0.15, self.event_loop.stop)
        self.event_loop.run_forever()
        self.assertEqual(caught, 1)

    def test_create_connection(self):
        # TODO: This depends on xkcd.com behavior!
        f = self.event_loop.create_connection(MyProto, 'xkcd.com', 80)
        tr, pr = self.event_loop.run_until_complete(f)
        self.assertTrue(isinstance(tr, transports.Transport))
        self.assertTrue(isinstance(pr, protocols.Protocol))
        self.event_loop.run()
        self.assertTrue(pr.nbytes > 0)

    def test_create_connection_sock(self):
        # TODO: This depends on xkcd.com behavior!
        sock = None
        infos = self.event_loop.run_until_complete(
            self.event_loop.getaddrinfo('xkcd.com', 80,type=socket.SOCK_STREAM))
        for family, type, proto, cname, address in infos:
            try:
                sock = socket.socket(family=family, type=type, proto=proto)
                sock.setblocking(False)
                self.event_loop.run_until_complete(
                    self.event_loop.sock_connect(sock, address))
            except:
                pass
            else:
                break

        f = self.event_loop.create_connection(MyProto, sock=sock)
        tr, pr = self.event_loop.run_until_complete(f)
        self.assertTrue(isinstance(tr, transports.Transport))
        self.assertTrue(isinstance(pr, protocols.Protocol))
        self.event_loop.run()
        self.assertTrue(pr.nbytes > 0)

    @unittest.skipIf(ssl is None, 'No ssl module')
    def test_create_ssl_connection(self):
        # TODO: This depends on xkcd.com behavior!
        f = self.event_loop.create_connection(
            MyProto, 'xkcd.com', 443, ssl=True)
        tr, pr = self.event_loop.run_until_complete(f)
        self.assertTrue(isinstance(tr, transports.Transport))
        self.assertTrue(isinstance(pr, protocols.Protocol))
        self.assertTrue('ssl' in tr.__class__.__name__.lower())
        self.assertTrue(hasattr(tr.get_extra_info('socket'), 'getsockname'))
        self.event_loop.run()
        self.assertTrue(pr.nbytes > 0)

    def test_create_connection_host_port_sock(self):
        self.suppress_log_errors()
        fut = self.event_loop.create_connection(
            MyProto, 'xkcd.com', 80, sock=object())
        self.assertRaises(ValueError, self.event_loop.run_until_complete, fut)

    def test_create_connection_no_host_port_sock(self):
        self.suppress_log_errors()
        fut = self.event_loop.create_connection(MyProto)
        self.assertRaises(ValueError, self.event_loop.run_until_complete, fut)

    def test_create_connection_no_getaddrinfo(self):
        self.suppress_log_errors()
        getaddrinfo = self.event_loop.getaddrinfo = unittest.mock.Mock()
        getaddrinfo.return_value = []

        fut = self.event_loop.create_connection(MyProto, 'xkcd.com', 80)
        self.assertRaises(
            socket.error, self.event_loop.run_until_complete, fut)

    def test_create_connection_connect_err(self):
        self.suppress_log_errors()
        self.event_loop.sock_connect = unittest.mock.Mock()
        self.event_loop.sock_connect.side_effect = socket.error

        fut = self.event_loop.create_connection(MyProto, 'xkcd.com', 80)
        self.assertRaises(
            socket.error, self.event_loop.run_until_complete, fut)

    def test_create_connection_mutiple_errors(self):
        self.suppress_log_errors()

        def getaddrinfo(*args, **kw):
            yield from []
            return [(2,1,6,'',('107.6.106.82',80)),
                    (2,1,6,'',('107.6.106.82',80))]
        self.event_loop.getaddrinfo = getaddrinfo
        self.event_loop.sock_connect = unittest.mock.Mock()
        self.event_loop.sock_connect.side_effect = socket.error

        fut = self.event_loop.create_connection(MyProto, 'xkcd.com', 80)
        self.assertRaises(
            socket.error, self.event_loop.run_until_complete, fut)

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
        self.event_loop.run_once(0.01) # for windows proactor selector
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
        self.event_loop.run_once(0.1) # for windows proactor selector
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
        fut = self.event_loop.start_serving(MyProto,'0.0.0.0',0,sock=object())
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
        m_socket.getaddrinfo.return_value = [(2, 1, 6, '', ('127.0.0.1',10100))]
        m_sock = m_socket.socket.return_value = unittest.mock.Mock()
        m_sock.setsockopt.side_effect = Err

        fut = self.event_loop.start_serving(MyProto, '0.0.0.0', 0)
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
        def test_reader_callback_with_handler(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_reader()")
        def test_writer_callback(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_writer()")
        def test_writer_callback_cancel(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_writer()")
        def test_writer_callback_with_handler(self):
            raise unittest.SkipTest("IocpEventLoop does not have add_writer()")
        def test_accept_connection_retry(self):
            raise unittest.SkipTest(
                "IocpEventLoop does not have _accept_connection()")
        def test_accept_connection_exception(self):
            raise unittest.SkipTest(
                "IocpEventLoop does not have _accept_connection()")

else:
    from tulip import selectors
    from tulip import unix_events

    if hasattr(selectors, 'KqueueSelector'):
        class KqueueEventLoopTests(EventLoopTestsMixin,
                                   test_utils.LogTrackingTestCase):
            def create_event_loop(self):
                return unix_events.SelectorEventLoop(selectors.KqueueSelector())

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


class HandlerTests(unittest.TestCase):

    def test_handler(self):
        def callback(*args):
            return args

        args = ()
        h = events.Handler(callback, args)
        self.assertIs(h.callback, callback)
        self.assertIs(h.args, args)
        self.assertFalse(h.cancelled)

        r = repr(h)
        self.assertTrue(r.startswith(
            'Handler('
            '<function HandlerTests.test_handler.<locals>.callback'))
        self.assertTrue(r.endswith('())'))

        h.cancel()
        self.assertTrue(h.cancelled)

        r = repr(h)
        self.assertTrue(r.startswith(
            'Handler('
            '<function HandlerTests.test_handler.<locals>.callback'))
        self.assertTrue(r.endswith('())<cancelled>'))

    def test_make_handler(self):
        def callback(*args):
            return args
        h1 = events.Handler(callback, ())
        h2 = events.make_handler(h1, ())
        self.assertIs(h1, h2)

        self.assertRaises(AssertionError,
                          events.make_handler, h1, (1,2,))


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

        h3 = events.Handler(callback, ())
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


if __name__ == '__main__':
    unittest.main()
