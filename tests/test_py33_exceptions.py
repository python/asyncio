# -*- coding: utf-8 -*-
"""
Tests for py33_exceptions.

"""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function


import unittest
from trollius import py33_exceptions

class TestWrapErrors(unittest.TestCase):

    def test_ebadf_wrapped_to_OSError(self):
        # https://github.com/jamadden/trollius/issues/17
        import socket
        import os
        import errno
        s = socket.socket()
        os.close(s.fileno())

        with self.assertRaises(socket.error) as exc:
            s.send(b'abc')

        self.assertEqual(exc.exception.errno, errno.EBADF)

        with self.assertRaises(OSError) as exc:
            py33_exceptions.wrap_error(s.send, b'abc')

        self.assertEqual(exc.exception.errno, errno.EBADF)

if __name__ == '__main__':
    unittest.main()
