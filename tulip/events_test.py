"""Tests for events.py."""

import concurrent.futures
import select
import socket
import threading
import time
import unittest

from . import events
from . import transports
from . import protocols
from . import unix_events


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
        pollster = self.POLLSTER_CLASS()
        event_loop = unix_events.UnixEventLoop(pollster)
        events.set_event_loop(event_loop)

    def testRun(self):
        el = events.get_event_loop()
        el.run()  # Returns immediately.

    def testCallLater(self):
        el = events.get_event_loop()
        results = []
        def callback(arg):
            results.append(arg)
        el.call_later(0.1, callback, 'hello world')
        t0 = time.monotonic()
        el.run()
        t1 = time.monotonic()
        self.assertEqual(results, ['hello world'])
        self.assertTrue(t1-t0 >= 0.09)

    def testCallRepeatedly(self):
        el = events.get_event_loop()
        results = []
        def callback(arg):
            results.append(arg)
        el.call_repeatedly(0.03, callback, 'ho')
        el.call_later(0.1, el.stop)
        el.run()
        self.assertEqual(results, ['ho', 'ho', 'ho'])

    def testCallSoon(self):
        el = events.get_event_loop()
        results = []
        def callback(arg1, arg2):
            results.append((arg1, arg2))
        el.call_soon(callback, 'hello', 'world')
        el.run()
        self.assertEqual(results, [('hello', 'world')])

    def testCallSoonThreadsafe(self):
        el = events.get_event_loop()
        results = []
        def callback(arg):
            results.append(arg)
        def run():
            el.call_soon_threadsafe(callback, 'hello')
        t = threading.Thread(target=run)
        el.call_later(0.1, callback, 'world')
        t0 = time.monotonic()
        t.start()
        el.run()
        t1 = time.monotonic()
        t.join()
        self.assertEqual(results, ['hello', 'world'])
        self.assertTrue(t1-t0 >= 0.09)

    def testWrapFuture(self):
        el = events.get_event_loop()
        def run(arg):
            time.sleep(0.1)
            return arg
        ex = concurrent.futures.ThreadPoolExecutor(1)
        f1 = ex.submit(run, 'oi')
        f2 = el.wrap_future(f1)
        res = el.run_until_complete(f2)
        self.assertEqual(res, 'oi')

    def testRunInExecutor(self):
        el = events.get_event_loop()
        def run(arg):
            time.sleep(0.1)
            return arg
        f2 = el.run_in_executor(None, run, 'yo')
        res = el.run_until_complete(f2)
        self.assertEqual(res, 'yo')

    def test_reader_callback(self):
        el = events.get_event_loop()
        r, w = unix_events.socketpair()
        bytes_read = []
        def reader():
            data = r.recv(1024)
            if data:
                bytes_read.append(data)
            else:
                el.remove_reader(r.fileno())
                r.close()
        el.add_reader(r.fileno(), reader)
        el.call_later(0.05, w.send, b'abc')
        el.call_later(0.1, w.send, b'def')
        el.call_later(0.15, w.close)
        el.run()
        self.assertEqual(b''.join(bytes_read), b'abcdef')

    def test_writer_callback(self):
        el = events.get_event_loop()
        r, w = unix_events.socketpair()
        el.add_writer(w.fileno(), w.send, b'x'*100)
        el.call_later(0.1, el.remove_writer, w.fileno())
        el.run()
        w.close()
        data = r.recv(32*1024)
        r.close()
        self.assertTrue(len(data) >= 200)

    def test_sock_client_ops(self):
        el = events.get_event_loop()
        sock = socket.socket()
        sock.setblocking(False)
        # TODO: This depends on python.org behavior!
        el.run_until_complete(el.sock_connect(sock, ('python.org', 80)))
        el.run_until_complete(el.sock_sendall(sock, b'GET / HTTP/1.0\r\n\r\n'))
        data = el.run_until_complete(el.sock_recv(sock, 1024))
        sock.close()
        self.assertTrue(data.startswith(b'HTTP/1.1 302 Found\r\n'))

    def test_sock_client_fail(self):
        el = events.get_event_loop()
        sock = socket.socket()
        sock.setblocking(False)
        # TODO: This depends on python.org behavior!
        with self.assertRaises(ConnectionRefusedError):
            el.run_until_complete(el.sock_connect(sock, ('python.org', 12345)))
        sock.close()

    def test_sock_accept(self):
        listener = socket.socket()
        listener.setblocking(False)
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        client = socket.socket()
        client.connect(listener.getsockname())
        el = events.get_event_loop()
        f = el.sock_accept(listener)
        conn, addr = el.run_until_complete(f)
        self.assertEqual(conn.gettimeout(), 0)
        self.assertEqual(addr, client.getsockname())
        self.assertEqual(client.getpeername(), listener.getsockname())
        client.close()
        conn.close()
        listener.close()

    def testCreateTransport(self):
        el = events.get_event_loop()
        # TODO: This depends on xkcd.com behavior!
        f = el.create_transport(MyProto, 'xkcd.com', 80)
        tr, pr = el.run_until_complete(f)
        self.assertTrue(isinstance(tr, transports.Transport))
        self.assertTrue(isinstance(pr, protocols.Protocol))
        el.run()
        self.assertTrue(pr.nbytes > 0)

    def testCreateSslTransport(self):
        el = events.get_event_loop()
        # TODO: This depends on xkcd.com behavior!
        f = el.create_transport(MyProto, 'xkcd.com', 443, ssl=True)
        tr, pr = el.run_until_complete(f)
        self.assertTrue(isinstance(tr, transports.Transport))
        self.assertTrue(isinstance(pr, protocols.Protocol))
        self.assertTrue('ssl' in tr.__class__.__name__.lower())
        el.run()
        self.assertTrue(pr.nbytes > 0)

    def testStartServing(self):
        el = events.get_event_loop()
        f = el.start_serving(MyProto, '0.0.0.0', 0)
        sock = el.run_until_complete(f)
        host, port = sock.getsockname()
        self.assertEqual(host, '0.0.0.0')
        client = socket.socket()
        client.connect((host, port))
        client.send(b'xxx')
        client.close()
        el.run_once()
        el.run_once()


if hasattr(select, 'kqueue'):
    class KqueueEventLoopTests(EventLoopTestsMixin, unittest.TestCase):
        POLLSTER_CLASS = unix_events.KqueuePollster


if hasattr(select, 'epoll'):
    class EPollEventLoopTests(EventLoopTestsMixin, unittest.TestCase):
        POLLSTER_CLASS = unix_events.EPollPollster


if hasattr(select, 'poll'):
    class PollEventLoopTests(EventLoopTestsMixin, unittest.TestCase):
        POLLSTER_CLASS = unix_events.PollPollster


# Should always exist.
class SelectEventLoopTests(EventLoopTestsMixin, unittest.TestCase):
    POLLSTER_CLASS = unix_events.SelectPollster


class HandlerTests(unittest.TestCase):

    def testHandler(self):
        pass


class PolicyTests(unittest.TestCase):

    def testPolicy(self):
        pass


if __name__ == '__main__':
    unittest.main()
