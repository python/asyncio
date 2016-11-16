"""asyncio.run() and asyncio.run_forever() functions."""

__all__ = ['run', 'run_forever']

import inspect
import threading

from . import coroutines
from . import events


def _cleanup(loop):
    try:
        # `shutdown_asyncgens` was added in Python 3.6; not all
        # event loops might support it.
        shutdown_asyncgens = loop.shutdown_asyncgens
    except AttributeError:
        pass
    else:
        loop.run_until_complete(shutdown_asyncgens())
    finally:
        events.set_event_loop(None)
        loop.close()


def run(main, *, debug=False):
    """Run a coroutine.

    This function runs the passed coroutine, taking care of
    managing the asyncio event loop and finalizing asynchronous
    generators.

    This function must be called from the main thread, and it
    cannot be called when another asyncio event loop is running.

    If debug is True, the event loop will be run in debug mode.

    This function should be used as a main entry point for
    asyncio programs, and should not be used to call asynchronous
    APIs.

    Example::

        async def main():
            await asyncio.sleep(1)
            print('hello')

        asyncio.run(main())
    """
    if events._get_running_loop() is not None:
        raise RuntimeError(
            "asyncio.run() cannot be called from a running event loop")
    if not isinstance(threading.current_thread(), threading._MainThread):
        raise RuntimeError(
            "asyncio.run() must be called from the main thread")
    if not coroutines.iscoroutine(main):
        raise ValueError("a coroutine was expected, got {!r}".format(main))

    loop = events.new_event_loop()
    try:
        events.set_event_loop(loop)

        if debug:
            loop.set_debug(True)

        return loop.run_until_complete(main)
    finally:
        _cleanup(loop)


def run_forever(main, *, debug=False):
    """Run asyncio loop.

    main must be an asynchronous generator with one yield, separating
    program initialization from cleanup logic.

    If debug is True, the event loop will be run in debug mode.

    This function should be used as a main entry point for
    asyncio programs, and should not be used to call asynchronous
    APIs.

    Example:

        async def main():
            server = await asyncio.start_server(...)
            try:
                yield  # <- Let event loop run forever.
            except KeyboardInterrupt:
                print('^C received; exiting.')
            finally:
                server.close()
                await server.wait_closed()

        asyncio.run_forever(main())
    """
    if not hasattr(inspect, 'isasyncgen'):
        raise NotImplementedError

    if events._get_running_loop() is not None:
        raise RuntimeError(
            "asyncio.run_forever() cannot be called from a running event loop")
    if not isinstance(threading.current_thread(), threading._MainThread):
        raise RuntimeError(
            "asyncio.run() must be called from the main thread")
    if not inspect.isasyncgen(main):
        raise ValueError(
            "an asynchronous generator was expected, got {!r}".format(main))

    loop = events.new_event_loop()
    try:
        events.set_event_loop(loop)
        if debug:
            loop.set_debug(True)

        ret = None
        try:
            ret = loop.run_until_complete(main.asend(None))
        except StopAsyncIteration as ex:
            return
        if ret is not None:
            raise RuntimeError("only empty yield is supported")

        yielded_twice = False
        try:
            loop.run_forever()
        except BaseException as ex:
            try:
                loop.run_until_complete(main.athrow(ex))
            except StopAsyncIteration as ex:
                pass
            else:
                yielded_twice = True
        else:
            try:
                loop.run_until_complete(main.asend(None))
            except StopAsyncIteration as ex:
                pass
            else:
                yielded_twice = True

        if yielded_twice:
            raise RuntimeError("only one yield is supported")

    finally:
        _cleanup(loop)
