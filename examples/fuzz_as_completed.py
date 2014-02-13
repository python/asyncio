#!/usr/bin/env python3

"""Fuzz tester for as_completed(), by Glenn Langford."""

import asyncio
import itertools
import random
import sys

@asyncio.coroutine
def sleeper(time):
    yield from asyncio.sleep(time)
    return time

@asyncio.coroutine
def watcher(tasks,delay=False):
    res = []
    for t in asyncio.as_completed(tasks):
        r = yield from t
        res.append(r)
        if delay:
            # simulate processing delay
            process_time = random.random() / 10
            yield from asyncio.sleep(process_time)
    #print(res)
    #assert(sorted(res) == res)
    if sorted(res) != res:
        print('FAIL', res)
        print('------------')
    else:
        print('.', end='')
        sys.stdout.flush()

loop = asyncio.get_event_loop()

print('Pass 1')
# All permutations of discrete task running times must be returned
# by as_completed in the correct order.
task_times = [0, 0.1, 0.2, 0.3, 0.4 ] # 120 permutations
for times in itertools.permutations(task_times):
    tasks = [ asyncio.Task(sleeper(t)) for t in times ]
    loop.run_until_complete(asyncio.Task(watcher(tasks)))

print()
print('Pass 2')
# Longer task times, with randomized duplicates. 100 tasks each time.
longer_task_times = [x/10 for x in range(30)]
for i in range(20):
    task_times = longer_task_times * 10
    random.shuffle(task_times)
    #print('Times', task_times[:500])
    tasks = [ asyncio.Task(sleeper(t)) for t in task_times[:100] ]
    loop.run_until_complete(asyncio.Task(watcher(tasks)))

print()
print('Pass 3')
# Same as pass 2, but with a random processing delay (0 - 0.1s) after
# retrieving each future from as_completed and 200 tasks. This tests whether
# the order that callbacks are triggered is preserved through to the
# as_completed caller.
for i in range(20):
    task_times = longer_task_times * 10
    random.shuffle(task_times)
    #print('Times', task_times[:200])
    tasks = [ asyncio.Task(sleeper(t)) for t in task_times[:200] ]
    loop.run_until_complete(asyncio.Task(watcher(tasks, delay=True)))

print()
loop.close()
