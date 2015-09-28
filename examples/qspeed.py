#!/usr/bin/env python3
"""How fast is the queue implementation?"""

import time
import asyncio
print(asyncio)

N_CONSUMERS = 10
N_PRODUCERS = 1
N_ITEMS = 100000  # Per producer
Q_SIZE = 1

@asyncio.coroutine
def producer(q):
    for i in range(N_ITEMS):
        yield from q.put(i)
    for i in range(N_CONSUMERS):
        yield from q.put(None)

@asyncio.coroutine
def consumer(q):
    while True:
        i = yield from q.get()
        if i is None:
            break

def main():
    q = asyncio.Queue(Q_SIZE)
    loop = asyncio.get_event_loop()
    consumers = [consumer(q) for _ in range(N_CONSUMERS)]
    producers = [producer(q) for _ in range(N_PRODUCERS)]
    t0 = time.time()
    loop.run_until_complete(asyncio.gather(*consumers, *producers))
    t1 = time.time()
    dt = t1 - t0
    print(N_CONSUMERS, 'consumers;',
          N_PRODUCERS, 'producers;',
          N_ITEMS, 'items/producer;',
          Q_SIZE, 'maxsize;',
          '%.3f total seconds;' % dt,
          '%.3f usec per item.' % (1e6*dt/N_ITEMS/N_PRODUCERS))

main()
