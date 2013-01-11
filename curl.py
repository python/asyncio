#!/usr/bin/env python3

import sys

import tulip
from tulip import http_client


def main():
    url = sys.argv[1]
    p = http_client.HttpClientProtocol(url)
    f = p.connect()
    t = p.event_loop.run_until_complete(tulip.Task(f))
    print('transport =', t)
    p.event_loop.run()


if __name__ == '__main__':
    main()
