"""Compare timing of plain vs. yield-from calls."""

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

def main():
    depth = 20

    t0 = time.time()
    k = plain(depth)
    t1 = time.time()
    print('plain', k, t1-t0)

    t0 = time.time()
    try:
        g = coroutine(depth)
        while True:
            next(g)
    except StopIteration as err:
        k = err.value
    t1 = time.time()
    print('coro.', k, t1-t0)

if __name__ == '__main__':
    main()
