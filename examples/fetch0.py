"""Simplest possible HTTP client."""

import sys

from asyncio import *


@coroutine
def fetch():
    r, w = yield from open_connection('python.org', 80)
    request = 'GET / HTTP/1.0\r\n\r\n'
    print('>', request, file=sys.stderr)
    w.write(request.encode('latin-1'))
    while True:
        line = yield from r.readline()
        line = line.decode('latin-1').rstrip()
        if not line:
            break
        print('<', line, file=sys.stderr)
    print(file=sys.stderr)
    body = yield from r.read()
    return body


def main():
    loop = get_event_loop()
    try:
        body = loop.run_until_complete(fetch())
    finally:
        loop.close()
    print(body.decode('latin-1'), end='')


if __name__ == '__main__':
    main()
