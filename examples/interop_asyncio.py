import asyncio
import trollius

@asyncio.coroutine
def asyncio_noop():
    pass

@asyncio.coroutine
def asyncio_coroutine(coro):
    print("asyncio coroutine")
    res = yield from coro
    print("asyncio inner coroutine result: %r" % (res,))
    print("asyncio coroutine done")
    return "asyncio"

@trollius.coroutine
def trollius_noop():
    pass

@trollius.coroutine
def trollius_coroutine(coro):
    print("trollius coroutine")
    res = yield trollius.From(coro)
    print("trollius inner coroutine result: %r" % (res,))
    print("trollius coroutine done")
    raise trollius.Return("trollius")

def main():
    # use trollius event loop policy in asyncio
    policy = trollius.get_event_loop_policy()
    asyncio.set_event_loop_policy(policy)

    # create an event loop for the main thread: use Trollius event loop
    loop = trollius.get_event_loop()
    assert asyncio.get_event_loop() is loop

    print("[ asyncio coroutine called from trollius coroutine ]")
    coro1 = asyncio_noop()
    coro2 = asyncio_coroutine(coro1)
    res = loop.run_until_complete(trollius_coroutine(coro2))
    print("trollius coroutine result: %r" % res)
    print("")

    print("[ asyncio coroutine called from trollius coroutine ]")
    coro1 = trollius_noop()
    coro2 = trollius_coroutine(coro1)
    res = loop.run_until_complete(asyncio_coroutine(coro2))
    print("asyncio coroutine result: %r" % res)
    print("")

    loop.close()

main()
