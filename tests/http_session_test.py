"""Tests for tulip/http/session.py"""

import http.cookies
import unittest
import unittest.mock

import tulip
import tulip.http

from tulip.http.client import HttpResponse
from tulip.http.session import Session


class HttpSessionTests(unittest.TestCase):

    def setUp(self):
        self.loop = tulip.new_event_loop()
        tulip.set_event_loop(self.loop)

        self.transport = unittest.mock.Mock()
        self.stream = tulip.StreamBuffer()
        self.response = HttpResponse('get', 'http://python.org')

    def tearDown(self):
        tulip.set_event_loop(None)
        self.loop.close()

    def test_del(self):
        session = Session()
        close = session.close = unittest.mock.Mock()

        del session
        self.assertTrue(close.called)

    def test_close(self):
        tr = unittest.mock.Mock()

        session = Session()
        session._conns[1] = [(tr, object())]
        session.close()

        self.assertFalse(session._conns)
        self.assertTrue(tr.close.called)

    def test_get(self):
        session = Session()
        self.assertEqual(session._get(1), (None, None))

        tr, proto = unittest.mock.Mock(), object()
        session._conns[1] = [(tr, proto)]
        self.assertEqual(session._get(1), (tr, proto))

    def test_release(self):
        session = Session()
        resp = unittest.mock.Mock()
        resp.message.should_close = False

        cookies = resp.cookies = http.cookies.SimpleCookie()
        cookies['c1'] = 'cookie1'
        cookies['c2'] = 'cookie2'

        tr, proto = unittest.mock.Mock(), unittest.mock.Mock()
        session._release(resp, 1, (tr, proto))
        self.assertEqual(session._conns[1][0], (tr, proto))
        self.assertEqual(session.cookies, dict(cookies.items()))

    def test_release_close(self):
        session = Session()
        resp = unittest.mock.Mock()
        resp.message.should_close = True

        cookies = resp.cookies = http.cookies.SimpleCookie()
        cookies['c1'] = 'cookie1'
        cookies['c2'] = 'cookie2'

        tr, proto = unittest.mock.Mock(), unittest.mock.Mock()
        session._release(resp, 1, (tr, proto))
        self.assertFalse(session._conns)
        self.assertTrue(tr.close.called)

    def test_call_new_conn_exc(self):
        tr, proto = unittest.mock.Mock(), unittest.mock.Mock()

        class Req:
            host = 'host'
            port = 80
            ssl = False

            def send(self, *args):
                raise ValueError()

        class Loop:
            @tulip.coroutine
            def create_connection(self, *args, **kw):
                return tr, proto

        session = Session()
        self.assertRaises(
            ValueError,
            self.loop.run_until_complete, session.start(Req(), Loop(), True))

        self.assertTrue(tr.close.called)

    def test_call_existing_conn_exc(self):
        existing = unittest.mock.Mock()
        tr, proto = unittest.mock.Mock(), unittest.mock.Mock()

        class Req:
            host = 'host'
            port = 80
            ssl = False

            def send(self, transport):
                if transport is existing:
                    transport.close()
                    raise ValueError()
                else:
                    return Resp()

        class Resp:
            @tulip.coroutine
            def start(self, *args, **kw):
                pass

        class Loop:
            @tulip.coroutine
            def create_connection(self, *args, **kw):
                return tr, proto

        session = Session()
        key = ('host', 80, False)
        session._conns[key] = [(existing, object())]

        resp = self.loop.run_until_complete(session.start(Req(), Loop()))
        self.assertIsInstance(resp, Resp)
        self.assertTrue(existing.close.called)
        self.assertFalse(session._conns[key])
