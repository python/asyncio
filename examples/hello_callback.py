"""Print 'Hello World' every two seconds, using a callback."""

import asyncio


def print_and_repeat(loop):
    print('Hello World')
    loop.call_later(2, print_and_repeat, loop)


if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    print_and_repeat(loop)
    try:
        loop.run_forever()
    finally:
        loop.close()
