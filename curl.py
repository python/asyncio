#!/usr/bin/env python3

import sys

import tulip
from tulip import http_client


def main():
    url = sys.argv[1]
    p = http_client.HttpClientProtocol(url)
    f = p.connect()
    p.event_loop.run_until_complete(tulip.Task(f))


if __name__ == '__main__':
    main()
