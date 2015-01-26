"""Print 'Hello World' every two seconds, using a coroutine."""

import asyncio


@asyncio.coroutine
def greet_every_two_seconds():
    while True:
        print('Hello World')
        yield from asyncio.sleep(2)


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(greet_every_two_seconds())
    finally:
        loop.close()
