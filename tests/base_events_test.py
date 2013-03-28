"""Tests for base_events.py"""

import concurrent.futures
import logging
import socket
import time
import unittest
import unittest.mock

from tulip import base_events
from tulip import events
from tulip import futures
from tulip import protocols
from tulip import tasks
from tulip import test_utils


class BaseEventLoopTests(test_utils.LogTrackingTestCase):

    def setUp(self):
        super().setUp()

        self.event_loop = base_events.BaseEventLoop()
        self.event_loop._selector = unittest.mock.Mock()
        self.event_loop._selector.registered_count.return_value = 1

    def test_not_implemented(self):
        m = unittest.mock.Mock()
        self.assertRaises(
            NotImplementedError,
            self.event_loop._make_socket_transport, m, m)
        self.assertRaises(
            NotImplementedError,
            self.event_loop._make_ssl_transport, m, m, m, m)
        self.assertRaises(
            NotImplementedError,
            self.event_loop._make_datagram_transport, m, m)
        self.assertRaises(
            NotImplementedError, self.event_loop._process_events, [])
        self.assertRaises(
            NotImplementedError, self.event_loop._write_to_self)
        self.assertRaises(
            NotImplementedError, self.event_loop._read_from_self)
        self.assertRaises(
            NotImplementedError,
            self.event_loop._make_read_pipe_transport, m, m)
        self.assertRaises(
            NotImplementedError,
            self.event_loop._make_write_pipe_transport, m, m)

    def test_add_callback_handle(self):
        h = events.Handle(lambda: False, ())

        self.event_loop._add_callback(h)
        self.assertFalse(self.event_loop._scheduled)
        self.assertIn(h, self.event_loop._ready)

    def test_add_callback_timer(self):
        when = time.monotonic()

        h1 = events.Timer(when, lambda: False, ())
        h2 = events.Timer(when+10.0, lambda: False, ())

        self.event_loop._add_callback(h2)
        self.event_loop._add_callback(h1)
        self.assertEqual([h1, h2], self.event_loop._scheduled)
        self.assertFalse(self.event_loop._ready)

    def test_add_callback_cancelled_handle(self):
        h = events.Handle(lambda: False, ())
        h.cancel()

        self.event_loop._add_callback(h)
        self.assertFalse(self.event_loop._scheduled)
        self.assertFalse(self.event_loop._ready)

    def test_wrap_future(self):
        f = futures.Future()
        self.assertIs(self.event_loop.wrap_future(f), f)

    def test_wrap_future_concurrent(self):
        f = concurrent.futures.Future()
        self.assertIsInstance(self.event_loop.wrap_future(f), futures.Future)

    def test_set_default_executor(self):
        executor = unittest.mock.Mock()
        self.event_loop.set_default_executor(executor)
        self.assertIs(executor, self.event_loop._default_executor)

    def test_getnameinfo(self):
        sockaddr = unittest.mock.Mock()
        self.event_loop.run_in_executor = unittest.mock.Mock()
        self.event_loop.getnameinfo(sockaddr)
        self.assertEqual(
            (None, socket.getnameinfo, sockaddr, 0),
            self.event_loop.run_in_executor.call_args[0])

    def test_call_soon(self):
        def cb():
            pass

        h = self.event_loop.call_soon(cb)
        self.assertEqual(h._callback, cb)
        self.assertIsInstance(h, events.Handle)
        self.assertIn(h, self.event_loop._ready)

    def test_call_later(self):
        def cb():
            pass

        h = self.event_loop.call_later(10.0, cb)
        self.assertIsInstance(h, events.Timer)
        self.assertIn(h, self.event_loop._scheduled)
        self.assertNotIn(h, self.event_loop._ready)

    def test_call_later_no_delay(self):
        def cb():
            pass

        h = self.event_loop.call_later(0, cb)
        self.assertIn(h, self.event_loop._ready)
        self.assertNotIn(h, self.event_loop._scheduled)

    def test_run_once_in_executor_handle(self):
        def cb():
            pass

        self.assertRaises(
            AssertionError, self.event_loop.run_in_executor,
            None, events.Handle(cb, ()), ('',))
        self.assertRaises(
            AssertionError, self.event_loop.run_in_executor,
            None, events.Timer(10, cb, ()))

    def test_run_once_in_executor_canceled(self):
        def cb():
            pass
        h = events.Handle(cb, ())
        h.cancel()

        f = self.event_loop.run_in_executor(None, h)
        self.assertIsInstance(f, futures.Future)
        self.assertTrue(f.done())

    def test_run_once_in_executor(self):
        def cb():
            pass
        h = events.Handle(cb, ())
        f = futures.Future()
        executor = unittest.mock.Mock()
        executor.submit.return_value = f

        self.event_loop.set_default_executor(executor)

        res = self.event_loop.run_in_executor(None, h)
        self.assertIs(f, res)

        executor = unittest.mock.Mock()
        executor.submit.return_value = f
        res = self.event_loop.run_in_executor(executor, h)
        self.assertIs(f, res)
        self.assertTrue(executor.submit.called)

    def test_run_once(self):
        self.event_loop._run_once = unittest.mock.Mock()
        self.event_loop._run_once.side_effect = base_events._StopError
        self.event_loop.run_once()
        self.assertTrue(self.event_loop._run_once.called)

    def test__run_once(self):
        h1 = events.Timer(time.monotonic() + 0.1, lambda: True, ())
        h2 = events.Timer(time.monotonic() + 10.0, lambda: True, ())

        h1.cancel()

        self.event_loop._process_events = unittest.mock.Mock()
        self.event_loop._scheduled.append(h1)
        self.event_loop._scheduled.append(h2)
        self.event_loop._run_once()

        t = self.event_loop._selector.select.call_args[0][0]
        self.assertTrue(9.99 < t < 10.1)
        self.assertEqual([h2], self.event_loop._scheduled)
        self.assertTrue(self.event_loop._process_events.called)

    def test__run_once_timeout(self):
        h = events.Timer(time.monotonic() + 10.0, lambda: True, ())

        self.event_loop._process_events = unittest.mock.Mock()
        self.event_loop._scheduled.append(h)
        self.event_loop._run_once(1.0)
        self.assertEqual((1.0,), self.event_loop._selector.select.call_args[0])

    def test__run_once_timeout_with_ready(self):
        # If event loop has ready callbacks, select timeout is always 0.
        h = events.Timer(time.monotonic() + 10.0, lambda: True, ())

        self.event_loop._process_events = unittest.mock.Mock()
        self.event_loop._scheduled.append(h)
        self.event_loop._ready.append(h)
        self.event_loop._run_once(1.0)

        self.assertEqual((0,), self.event_loop._selector.select.call_args[0])

    @unittest.mock.patch('tulip.base_events.time')
    @unittest.mock.patch('tulip.base_events.tulip_log')
    def test__run_once_logging(self, m_logging, m_time):
        # Log to INFO level if timeout > 1.0 sec.
        idx = -1
        data = [10.0, 10.0, 12.0, 13.0]

        def monotonic():
            nonlocal data, idx
            idx += 1
            return data[idx]

        m_time.monotonic = monotonic
        m_logging.INFO = logging.INFO
        m_logging.DEBUG = logging.DEBUG

        self.event_loop._scheduled.append(events.Timer(11.0, lambda: True, ()))
        self.event_loop._process_events = unittest.mock.Mock()
        self.event_loop._run_once()
        self.assertEqual(logging.INFO, m_logging.log.call_args[0][0])

        idx = -1
        data = [10.0, 10.0, 10.3, 13.0]
        self.event_loop._scheduled = [events.Timer(11.0, lambda:True, ())]
        self.event_loop._run_once()
        self.assertEqual(logging.DEBUG, m_logging.log.call_args[0][0])

    def test__run_once_schedule_handle(self):
        handle = None
        processed = False

        def cb(event_loop):
            nonlocal processed, handle
            processed = True
            handle = event_loop.call_soon(lambda: True)

        h = events.Timer(time.monotonic() - 1, cb, (self.event_loop,))

        self.event_loop._process_events = unittest.mock.Mock()
        self.event_loop._scheduled.append(h)
        self.event_loop._run_once()

        self.assertTrue(processed)
        self.assertEqual([handle], list(self.event_loop._ready))

    def test_run_until_complete_assertion(self):
        self.assertRaises(
            AssertionError, self.event_loop.run_until_complete, 'blah')

    @unittest.mock.patch('tulip.base_events.socket')
    def test_create_connection_mutiple_errors(self, m_socket):
        self.suppress_log_errors()

        class MyProto(protocols.Protocol):
            pass

        def getaddrinfo(*args, **kw):
            yield from []
            return [(2, 1, 6, '', ('107.6.106.82', 80)),
                    (2, 1, 6, '', ('107.6.106.82', 80))]

        idx = -1
        errors = ['err1', 'err2']

        def _socket(*args, **kw):
            nonlocal idx, errors
            idx += 1
            raise socket.error(errors[idx])

        m_socket.socket = _socket
        m_socket.error = socket.error

        self.event_loop.getaddrinfo = getaddrinfo

        task = tasks.Task(
            self.event_loop.create_connection(MyProto, 'example.com', 80))
        task._step()
        exc = task.exception()
        self.assertEqual("Multiple exceptions: err1, err2", str(exc))
