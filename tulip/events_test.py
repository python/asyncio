"""Tests for events.py."""

import unittest

from . import events


class EventLoopTests(unittest.TestCase):

    def testRun(self):
        el = events.get_event_loop()
        el.run()  # Returns immediately.


class DelayedCallTests(unittest.TestCase):

    def testDelayedCall(self):
        pass


class PolicyTests(unittest.TestCase):

    def testPolicy(self):
        pass


if __name__ == '__main__':
    unittest.main()
