from trollius import test_utils
from trollius import From, Return
import trollius
import trollius.coroutines
from trollius.test_utils import unittest

try:
    import asyncio
except ImportError:
    from trollius.test_utils import SkipTest
    raise SkipTest('need asyncio')


@asyncio.coroutine
def asyncio_noop(value):
    yield from []
    return (value,)

@asyncio.coroutine
def asyncio_coroutine(coro, value):
    res = yield from coro
    return res + (value,)

@trollius.coroutine
def trollius_noop(value):
    yield From(None)
    raise Return((value,))

@trollius.coroutine
def trollius_coroutine(coro, value):
    res = yield trollius.From(coro)
    raise trollius.Return(res + (value,))


class AsyncioTests(test_utils.TestCase):
    def setUp(self):
        policy = trollius.get_event_loop_policy()

        asyncio.set_event_loop_policy(policy)
        self.addCleanup(asyncio.set_event_loop_policy, None)

        self.loop = policy.new_event_loop()
        self.addCleanup(self.loop.close)
        policy.set_event_loop(self.loop)

    def test_policy(self):
        self.assertIs(asyncio.get_event_loop(), self.loop)

    def test_asyncio(self):
        coro = asyncio_noop("asyncio")
        res = self.loop.run_until_complete(coro)
        self.assertEqual(res, ("asyncio",))

    def test_asyncio_in_trollius(self):
        coro1 = asyncio_noop(1)
        coro2 = asyncio_coroutine(coro1, 2)
        res = self.loop.run_until_complete(trollius_coroutine(coro2, 3))
        self.assertEqual(res, (1, 2, 3))

    def test_trollius_in_asyncio(self):
        coro1 = trollius_noop(4)
        coro2 = trollius_coroutine(coro1, 5)
        res = self.loop.run_until_complete(asyncio_coroutine(coro2, 6))
        self.assertEqual(res, (4, 5, 6))

    def test_step_future(self):
        old_debug = trollius.coroutines._DEBUG
        try:
            def step_future():
                future = asyncio.Future()
                self.loop.call_soon(future.set_result, "asyncio.Future")
                return (yield from future)

            # test in release mode
            trollius.coroutines._DEBUG = False
            result = self.loop.run_until_complete(step_future())
            self.assertEqual(result, "asyncio.Future")

            # test in debug mode
            trollius.coroutines._DEBUG = True
            result = self.loop.run_until_complete(step_future())
            self.assertEqual(result, "asyncio.Future")
        finally:
            trollius.coroutines._DEBUG = old_debug

    def test_async(self):
        fut = asyncio.Future()
        self.assertIs(fut._loop, self.loop)

        fut2 = trollius.ensure_future(fut)
        self.assertIs(fut2, fut)
        self.assertIs(fut._loop, self.loop)

    def test_wrap_future(self):
        fut = asyncio.Future()
        self.assertIs(trollius.wrap_future(fut), fut)

    def test_run_until_complete(self):
        fut = asyncio.Future()
        fut.set_result("ok")
        self.assertEqual(self.loop.run_until_complete(fut),
                         "ok")

    def test_coroutine_decorator(self):
        @trollius.coroutine
        def asyncio_future(fut):
            return fut

        fut = asyncio.Future()
        self.loop.call_soon(fut.set_result, 'ok')
        res = self.loop.run_until_complete(asyncio_future(fut))
        self.assertEqual(res, "ok")

    def test_as_completed(self):
        fut = asyncio.Future()
        fut.set_result("ok")

        with self.assertRaises(TypeError):
            for f in trollius.as_completed(fut):
                pass

        @trollius.coroutine
        def get_results(fut):
            results = []
            for f in trollius.as_completed([fut]):
                res = yield trollius.From(f)
                results.append(res)
            raise trollius.Return(results)

        results = self.loop.run_until_complete(get_results(fut))
        self.assertEqual(results, ["ok"])

    def test_gather(self):
        fut = asyncio.Future()
        fut.set_result("ok")
        results = self.loop.run_until_complete(trollius.gather(fut))
        self.assertEqual(results, ["ok"])


if __name__ == '__main__':
    unittest.main()
