'''Example app using `file_async` and cancellations.'''

__author__ = 'Guido van Rossum <guido@python.org>'

import time

import scheduling

def binary(n):
    if n <= 0:
        return 1
    l = yield from binary(n-1)
    r = yield from binary(n-1)
    return l + 1 + r

def doit(depth):
    t0 = time.time()
    k = yield from binary(depth)
    t1 = time.time()
    print(depth, k, round(t1-t0, 6))
    return (depth, k, round(t1-t0, 6))

def main():
    for depth in range(20):
        yield from doit(depth)

import logging
logging.basicConfig(level=logging.DEBUG)

scheduling.run(main())
