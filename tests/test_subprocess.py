import asyncio
import asyncio.subprocess
import os
import sys
import unittest

class SubprocessTestCase(unittest.TestCase):
    def setUp(self):
        policy = asyncio.get_event_loop_policy()
        if os.name == 'nt':
            self.loop = asyncio.ProactorEventLoop()
        else:
            self.loop = policy.new_event_loop()
        # ensure that the event loop is passed explicitly in the code
        policy.set_event_loop(None)

    def test_shell(self):
        args = [sys.executable, '-c', 'pass']

        @asyncio.coroutine
        def run():
            return (yield from asyncio.subprocess.call(*args, loop=self.loop))

        exitcode = self.loop.run_until_complete(run())
        self.assertEqual(exitcode, 0)


if __name__ == '__main__':
    unittest.main()
