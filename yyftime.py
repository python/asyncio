"""Compare timing of yield-from <generator> vs. yield <future> calls."""

import gc
import time

def coroutine(n):
    if n <= 0:
        return 1
    l = yield from coroutine(n-1)
    r = yield from coroutine(n-1)
    return l + 1 + r

def run_coro(depth):
    t0 = time.time()
    try:
        g = coroutine(depth)
        while True:
            next(g)
    except StopIteration as err:
        k = err.value
        t1 = time.time()
        print('coro', depth, k, round(t1-t0, 6))
        return t1-t0

class Future:

    def __init__(self, g):
        self.g = g

    def wait(self):
        value = None
        try:
            while True:
                f = self.g.send(value)
                f.wait()
                value = f.value
        except StopIteration as err:
            self.value = err.value



def task(func):  # Decorator
    def wrapper(*args):
        g = func(*args)
        f = Future(g)
        return f
    return wrapper

@task
def oldstyle(n):
    if n <= 0:
        return 1
    l = yield oldstyle(n-1)
    r = yield oldstyle(n-1)
    return l + 1 + r

def run_olds(depth):
    t0 = time.time()
    f = oldstyle(depth)
    f.wait()
    k = f.value
    t1 = time.time()
    print('olds', depth, k, round(t1-t0, 6))
    return t1-t0

def main():
    gc.disable()
    for depth in range(16):
        tc = run_coro(depth)
        to = run_olds(depth)
        if tc:
            print('ratio', round(to/tc, 2))

if __name__ == '__main__':
    main()
