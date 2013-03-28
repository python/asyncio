"""Utilities shared by tests."""

import logging
import socket
import sys
import unittest


if sys.platform == 'win32':  # pragma: no cover
    from .winsocketpair import socketpair
else:
    from socket import socketpair  # pragma: no cover


class LogTrackingTestCase(unittest.TestCase):

    def setUp(self):
        self._logger = logging.getLogger()
        self._log_level = self._logger.getEffectiveLevel()

    def tearDown(self):
        self._logger.setLevel(self._log_level)

    def suppress_log_errors(self):  # pragma: no cover
        if self._log_level >= logging.WARNING:
            self._logger.setLevel(logging.CRITICAL)

    def suppress_log_warnings(self):  # pragma: no cover
        if self._log_level >= logging.WARNING:
            self._logger.setLevel(logging.ERROR)
