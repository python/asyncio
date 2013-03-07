"""Utilities shared by tests."""

import logging
import socket
import unittest


try:
    from socket import socketpair
except ImportError:
    from .winsocketpair import socketpair


class LogTrackingTestCase(unittest.TestCase):

    def setUp(self):
        self._logger = logging.getLogger()
        self._log_level = self._logger.getEffectiveLevel()

    def tearDown(self):
        self._logger.setLevel(self._log_level)

    def suppress_log_errors(self):
        if self._log_level >= logging.WARNING:
            self._logger.setLevel(logging.CRITICAL)

    def suppress_log_warnings(self):
        if self._log_level >= logging.WARNING:
            self._logger.setLevel(logging.ERROR)
