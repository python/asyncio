"""Tests for winsocketpair.py"""

import errno
import socket
import unittest
import unittest.mock

from tulip import winsocketpair


class WinsocketpairTests(unittest.TestCase):

    def test_winsocketpair(self):
        ssock, csock = winsocketpair.socketpair()

        csock.send(b'xxx')
        self.assertEqual(b'xxx', ssock.recv(1024))

        csock.close()
        ssock.close()

    @unittest.mock.patch('tulip.winsocketpair.socket')
    def test_winsocketpair_exc(self, m_socket):
        m_socket.error = socket.error
        class Err(socket.error):
            errno = errno.WSAEWOULDBLOCK + 1

        m_socket.socket.return_value.getsockname.return_value = ('', 12345)
        m_socket.socket.return_value.accept.return_value = object(), object()
        m_socket.socket.return_value.connect.side_effect = Err

        self.assertRaises(Err, winsocketpair.socketpair)
