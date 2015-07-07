"""Print 'Hello World' every two seconds, using a coroutine."""

import trollius
from trollius import From


@trollius.coroutine
def greet_every_two_seconds():
    while True:
        print('Hello World')
        yield From(trollius.sleep(2))


if __name__ == '__main__':
    loop = trollius.get_event_loop()
    try:
        loop.run_until_complete(greet_every_two_seconds())
    finally:
        loop.close()
