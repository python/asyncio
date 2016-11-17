"""Tests asyncio.run() and asyncio.run_forever()."""

import asyncio
import unittest
import sys

from unittest import mock


class TestPolicy(asyncio.AbstractEventLoopPolicy):

    def __init__(self, loop_factory):
        self.loop_factory = loop_factory
        self.loop = None

    def get_event_loop(self):
        # shouldn't ever be called by asyncio.run()
        # or asyncio.run_forever()
        raise RuntimeError

    def new_event_loop(self):
        return self.loop_factory()

    def set_event_loop(self, loop):
        if loop is not None:
            # we want to check if the loop is closed
            # in BaseTest.tearDown
            self.loop = loop


class BaseTest(unittest.TestCase):

    def new_loop(self):
        loop = asyncio.BaseEventLoop()
        loop._process_events = mock.Mock()
        loop._selector = mock.Mock()
        loop._selector.select.return_value = ()
        loop.shutdown_ag_run = False

        async def shutdown_asyncgens():
            loop.shutdown_ag_run = True
        loop.shutdown_asyncgens = shutdown_asyncgens

        return loop

    def setUp(self):
        super().setUp()

        policy = TestPolicy(self.new_loop)
        asyncio.set_event_loop_policy(policy)

    def tearDown(self):
        policy = asyncio.get_event_loop_policy()
        if policy.loop is not None:
            self.assertTrue(policy.loop.is_closed())
            self.assertTrue(policy.loop.shutdown_ag_run)

        asyncio.set_event_loop_policy(None)
        super().tearDown()


class RunTests(BaseTest):

    def test_asyncio_run_return(self):
        async def main():
            await asyncio.sleep(0)
            return 42

        self.assertEqual(asyncio.run(main()), 42)

    def test_asyncio_run_raises(self):
        async def main():
            await asyncio.sleep(0)
            raise ValueError('spam')

        with self.assertRaisesRegex(ValueError, 'spam'):
            asyncio.run(main())

    def test_asyncio_run_only_coro(self):
        for o in {1, lambda: None}:
            with self.subTest(obj=o), \
                    self.assertRaisesRegex(ValueError,
                                           'a coroutine was expected'):
                asyncio.run(o)

    def test_asyncio_run_debug(self):
        async def main(expected):
            loop = asyncio.get_event_loop()
            self.assertIs(loop.get_debug(), expected)

        asyncio.run(main(False))
        asyncio.run(main(True), debug=True)

    def test_asyncio_run_from_running_loop(self):
        async def main():
            asyncio.run(main())

        with self.assertRaisesRegex(RuntimeError,
                                    'cannot be called from a running'):
            asyncio.run(main())


class RunForeverTests(BaseTest):

    def stop_soon(self, *, exc=None):
        loop = asyncio.get_event_loop()

        if exc:
            def throw():
                raise exc
            loop.call_later(0.01, throw)
        else:
            loop.call_later(0.01, loop.stop)

    def test_asyncio_run_forever_return(self):
        async def main():
            if 0:
                yield
            return

        self.assertIsNone(asyncio.run_forever(main()))

    def test_asyncio_run_forever_non_none_yield(self):
        async def main():
            yield 1

        with self.assertRaisesRegex(RuntimeError, 'one empty yield'):
            self.assertIsNone(asyncio.run_forever(main()))

    def test_asyncio_run_forever_try_finally(self):
        DONE = 0

        async def main():
            nonlocal DONE
            self.stop_soon()
            try:
                yield
            finally:
                DONE += 1

        asyncio.run_forever(main())
        self.assertEqual(DONE, 1)

    def test_asyncio_run_forever_raises_before_yield(self):
        async def main():
            await asyncio.sleep(0)
            raise ValueError('spam')
            yield

        with self.assertRaisesRegex(ValueError, 'spam'):
            asyncio.run_forever(main())

    def test_asyncio_run_forever_raises_after_yield(self):
        async def main():
            self.stop_soon()
            yield
            raise ValueError('spam')

        with self.assertRaisesRegex(ValueError, 'spam'):
            asyncio.run_forever(main())

    def test_asyncio_run_forever_two_yields(self):
        async def main():
            self.stop_soon()
            yield
            yield
            raise ValueError('spam')

        with self.assertRaisesRegex(RuntimeError, 'one empty yield'):
            asyncio.run_forever(main())

    def test_asyncio_run_forever_only_ag(self):
        async def coro():
            pass

        for o in {1, lambda: None, coro()}:
            with self.subTest(obj=o), \
                    self.assertRaisesRegex(ValueError,
                                           'an asynchronous.*was expected'):
                asyncio.run_forever(o)

    def test_asyncio_run_forever_debug(self):
        async def main(expected):
            loop = asyncio.get_event_loop()
            self.assertIs(loop.get_debug(), expected)
            if 0:
                yield

        asyncio.run_forever(main(False))
        asyncio.run_forever(main(True), debug=True)

    def test_asyncio_run_forever_from_running_loop(self):
        async def main():
            asyncio.run_forever(main())
            if 0:
                yield

        with self.assertRaisesRegex(RuntimeError,
                                    'cannot be called from a running'):
            asyncio.run_forever(main())

    def test_asyncio_run_forever_base_exception(self):
        vi = sys.version_info
        if vi[:2] != (3, 6) or vi.releaselevel == 'beta' and vi.serial < 4:
            # See http://bugs.python.org/issue28721 for details.
            raise unittest.SkipTest(
                'this test requires Python 3.6b4 or greater')

        DONE = 0

        class MyExc(BaseException):
            pass

        async def main():
            nonlocal DONE
            self.stop_soon(exc=MyExc)
            try:
                yield
            except MyExc:
                DONE += 1

        asyncio.run_forever(main())
        self.assertEqual(DONE, 1)
