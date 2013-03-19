"""Tests for transports.py."""

import unittest
import unittest.mock

from tulip import transports


class TransportTests(unittest.TestCase):

    def test_ctor_extra_is_none(self):
        transport = transports.Transport()
        self.assertEqual(transport._extra, {})

    def test_get_extra_info(self):
        transport = transports.Transport({'extra': 'info'})
        self.assertEqual('info', transport.get_extra_info('extra'))
        self.assertIsNone(transport.get_extra_info('unknown'))

        default = object()
        self.assertIs(default, transport.get_extra_info('unknown', default))

    def test_writelines(self):
        transport = transports.Transport()
        transport.write = unittest.mock.Mock()

        transport.writelines(['line1', 'line2', 'line3'])
        self.assertEqual(3, transport.write.call_count)

    def test_not_implemented(self):
        transport = transports.Transport()

        self.assertRaises(NotImplementedError, transport.write, 'data')
        self.assertRaises(NotImplementedError, transport.write_eof)
        self.assertRaises(NotImplementedError, transport.can_write_eof)
        self.assertRaises(NotImplementedError, transport.pause)
        self.assertRaises(NotImplementedError, transport.resume)
        self.assertRaises(NotImplementedError, transport.close)
        self.assertRaises(NotImplementedError, transport.abort)
