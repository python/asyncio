"""Tests for base_events.py"""

import unittest
import unittest.mock

from . import base_events


class BaseEventLoopTests(unittest.TestCase):

    def test_not_implemented(self):
        base_event_loop = base_events.BaseEventLoop()

        m = unittest.mock.Mock()
        self.assertRaises(
            NotImplementedError,
            base_event_loop._make_socket_transport, m, m)
        self.assertRaises(
            NotImplementedError,
            base_event_loop._make_ssl_transport, m, m, m, m)
        self.assertRaises(
            NotImplementedError,
            base_event_loop._process_events, [])
