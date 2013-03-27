"""Compare timing of plain vs. yield-from calls."""

import gc
import time

def plain(n):
    if n <= 0:
        return 1
    l = plain(n-1)
    r = plain(n-1)
    return l + 1 + r

def coroutine(n):
    if n <= 0:
        return 1
    l = yield from coroutine(n-1)
    r = yield from coroutine(n-1)
    return l + 1 + r

def submain(depth):
    t0 = time.time()
    k = plain(depth)
    t1 = time.time()
    fmt = ' {} {} {:-9,.5f}'
    delta0 = t1-t0
    print(('plain' + fmt).format(depth, k, delta0))

    t0 = time.time()
    try:
        g = coroutine(depth)
        while True:
            next(g)
    except StopIteration as err:
        k = err.value
        t1 = time.time()
        delta1 = t1-t0
        print(('coro.' + fmt).format(depth, k, delta1))
    if delta0:
        print(('relat' + fmt).format(depth, k, delta1/delta0))

def main(reasonable=16):
    gc.disable()
    for depth in range(reasonable):
        submain(depth)

if __name__ == '__main__':
    main()
