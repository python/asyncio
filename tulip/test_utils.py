"""Utilities shared by tests."""

import contextlib
import logging
import os
import socket
import sys
import threading
import unittest
try:
    import ssl
except ImportError:  # pragma: no cover
    ssl = None

import tulip
import tulip.http


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


@contextlib.contextmanager
def run_test_server(loop, *, host='127.0.0.1', port=0, use_ssl=False):
    class HttpServer(tulip.http.ServerHttpProtocol):
        def handle_request(self, info, message):
            response = tulip.http.Response(
                self.transport, 200, info.version)

            text = b'Test message'
            response.add_header('Content-type', 'text/plain')
            response.add_header('Content-length', str(len(text)))
            response.send_headers()
            response.write(text)
            response.write_eof()
            self.transport.close()

    if use_ssl:
        here = os.path.join(os.path.dirname(__file__), '..', 'tests')
        keyfile = os.path.join(here, 'sample.key')
        certfile = os.path.join(here, 'sample.crt')
        sslcontext = ssl.SSLContext(ssl.PROTOCOL_SSLv23)
        sslcontext.load_cert_chain(certfile, keyfile)
    else:
        sslcontext = False

    def run(loop, fut):
        thread_loop = tulip.new_event_loop()
        tulip.set_event_loop(thread_loop)

        sock = thread_loop.run_until_complete(
            thread_loop.start_serving(HttpServer, host, port, ssl=sslcontext))

        waiter = tulip.Future()
        loop.call_soon_threadsafe(
            fut.set_result, (thread_loop, waiter, sock.getsockname()))

        thread_loop.run_until_complete(waiter)
        thread_loop.stop()

    fut = tulip.Future()
    server_thread = threading.Thread(target=run, args=(loop, fut))
    server_thread.start()

    thread_loop, waiter, addr = loop.run_until_complete(fut)
    try:
        yield addr
    finally:
        thread_loop.call_soon_threadsafe(waiter.set_result, None)
        server_thread.join()
