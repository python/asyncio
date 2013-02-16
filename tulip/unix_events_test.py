"""Tests for unix_events.py."""

import errno
import unittest
import unittest.mock

try:
    import signal
except ImportError:
    signal = None

from . import events
from . import unix_events


@unittest.skipUnless(signal, 'Signals are not supported')
class SelectorEventLoopTests(unittest.TestCase):

    def setUp(self):
        self.event_loop = unix_events.SelectorEventLoop()

    def test_check_signal(self):
        self.assertRaises(
            TypeError, self.event_loop._check_signal, '1')
        self.assertRaises(
            ValueError, self.event_loop._check_signal, signal.NSIG + 1)

        unix_events.signal = None

        def restore_signal():
            unix_events.signal = signal
        self.addCleanup(restore_signal)

        self.assertRaises(
            RuntimeError, self.event_loop._check_signal, signal.SIGINT)

    def test_handle_signal_no_handler(self):
        self.event_loop._handle_signal(signal.NSIG + 1, ())

    @unittest.mock.patch('tulip.unix_events.signal')
    def test_add_signal_handler_setup_error(self, m_signal):
        m_signal.NSIG = signal.NSIG
        m_signal.set_wakeup_fd.side_effect = ValueError

        self.assertRaises(
            RuntimeError,
            self.event_loop.add_signal_handler,
            signal.SIGINT, lambda: True)

    @unittest.mock.patch('tulip.unix_events.signal')
    def test_add_signal_handler(self, m_signal):
        m_signal.NSIG = signal.NSIG

        h = self.event_loop.add_signal_handler(signal.SIGHUP, lambda: True)
        self.assertIsInstance(h, events.Handler)

    @unittest.mock.patch('tulip.unix_events.signal')
    def test_add_signal_handler_install_error(self, m_signal):
        m_signal.NSIG = signal.NSIG

        def set_wakeup_fd(fd):
            if fd == -1:
                raise ValueError()
        m_signal.set_wakeup_fd = set_wakeup_fd

        class Err(OSError):
            errno = errno.EFAULT
        m_signal.signal.side_effect = Err

        self.assertRaises(
            Err,
            self.event_loop.add_signal_handler,
            signal.SIGINT, lambda: True)

    @unittest.mock.patch('tulip.unix_events.signal')
    @unittest.mock.patch('tulip.unix_events.logging')
    def test_add_signal_handler_install_error2(self, m_logging, m_signal):
        m_signal.NSIG = signal.NSIG

        class Err(OSError):
            errno = errno.EINVAL
        m_signal.signal.side_effect = Err

        self.event_loop._signal_handlers[signal.SIGHUP] = lambda: True
        self.assertRaises(
            RuntimeError,
            self.event_loop.add_signal_handler,
            signal.SIGINT, lambda: True)
        self.assertFalse(m_logging.info.called)
        self.assertEqual(1, m_signal.set_wakeup_fd.call_count)

    @unittest.mock.patch('tulip.unix_events.signal')
    @unittest.mock.patch('tulip.unix_events.logging')
    def test_add_signal_handler_install_error3(self, m_logging, m_signal):
        class Err(OSError):
            errno = errno.EINVAL
        m_signal.signal.side_effect = Err
        m_signal.NSIG = signal.NSIG

        self.assertRaises(
            RuntimeError,
            self.event_loop.add_signal_handler,
            signal.SIGINT, lambda: True)
        self.assertFalse(m_logging.info.called)
        self.assertEqual(2, m_signal.set_wakeup_fd.call_count)

    @unittest.mock.patch('tulip.unix_events.signal')
    def test_remove_signal_handler(self, m_signal):
        m_signal.NSIG = signal.NSIG

        h = self.event_loop.add_signal_handler(signal.SIGHUP, lambda: True)

        self.assertTrue(
            self.event_loop.remove_signal_handler(signal.SIGHUP))
        self.assertTrue(m_signal.set_wakeup_fd.called)
        self.assertTrue(m_signal.signal.called)
        self.assertEqual(
            (signal.SIGHUP, m_signal.SIG_DFL), m_signal.signal.call_args[0])

    @unittest.mock.patch('tulip.unix_events.signal')
    def test_remove_signal_handler_2(self, m_signal):
        m_signal.NSIG = signal.NSIG
        m_signal.SIGINT = signal.SIGINT

        h = self.event_loop.add_signal_handler(signal.SIGINT, lambda: True)
        self.event_loop._signal_handlers[signal.SIGHUP] = object()
        m_signal.set_wakeup_fd.reset_mock()

        self.assertTrue(
            self.event_loop.remove_signal_handler(signal.SIGINT))
        self.assertFalse(m_signal.set_wakeup_fd.called)
        self.assertTrue(m_signal.signal.called)
        self.assertEqual(
            (signal.SIGINT, m_signal.default_int_handler),
            m_signal.signal.call_args[0])

    @unittest.mock.patch('tulip.unix_events.signal')
    @unittest.mock.patch('tulip.unix_events.logging')
    def test_remove_signal_handler_cleanup_error(self, m_logging, m_signal):
        m_signal.NSIG = signal.NSIG
        h = self.event_loop.add_signal_handler(signal.SIGHUP, lambda: True)

        m_signal.set_wakeup_fd.side_effect = ValueError

        self.event_loop.remove_signal_handler(signal.SIGHUP)
        self.assertTrue(m_logging.info)

    @unittest.mock.patch('tulip.unix_events.signal')
    def test_remove_signal_handler_error(self, m_signal):
        m_signal.NSIG = signal.NSIG
        h = self.event_loop.add_signal_handler(signal.SIGHUP, lambda: True)

        m_signal.signal.side_effect = OSError

        self.assertRaises(
            OSError, self.event_loop.remove_signal_handler, signal.SIGHUP)

    @unittest.mock.patch('tulip.unix_events.signal')
    def test_remove_signal_handler_error2(self, m_signal):
        m_signal.NSIG = signal.NSIG
        h = self.event_loop.add_signal_handler(signal.SIGHUP, lambda: True)

        class Err(OSError):
            errno = errno.EINVAL
        m_signal.signal.side_effect = Err

        self.assertRaises(
            RuntimeError, self.event_loop.remove_signal_handler, signal.SIGHUP)
