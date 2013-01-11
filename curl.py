#!/usr/bin/env python3

import sys
import urllib.parse

import tulip
from tulip import http_client


def main():
    url = sys.argv[1]
    scheme, netloc, path, query, fragment = urllib.parse.urlsplit(url)
    if not path:
        path = '/'
    if query:
        path = '?'.join([path, query])
    print(netloc, path, scheme)
    p = http_client.HttpClientProtocol(netloc, path=path,
                                       ssl=(scheme=='https'))
    f = p.connect()
    p.event_loop.run_until_complete(tulip.Task(f))


if __name__ == '__main__':
    main()
