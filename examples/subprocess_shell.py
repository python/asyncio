"""Example writing to and reading from a subprocess at the same time using
tasks."""

import asyncio
import os
from asyncio.subprocess import PIPE


@asyncio.coroutine
def send_input(writer, input):
    try:
        for line in input:
            print('sending', len(line), 'bytes')
            writer.write(line)
            d = writer.drain()
            if d:
                print('pause writing')
                yield from d
                print('resume writing')
        writer.close()
    except BrokenPipeError:
        print('stdin: broken pipe error')
    except ConnectionResetError:
        print('stdin: connection reset error')

@asyncio.coroutine
def log_errors(reader):
    while True:
        line = yield from reader.readline()
        if not line:
            break
        print('ERROR', repr(line))

@asyncio.coroutine
def read_stdout(stdout):
    while True:
        line = yield from stdout.readline()
        print('received', repr(line))
        if not line:
            break

@asyncio.coroutine
def start(cmd, input=None, **kwds):
    kwds['stdout'] = PIPE
    kwds['stderr'] = PIPE
    if input is None and 'stdin' not in kwds:
        kwds['stdin'] = None
    else:
        kwds['stdin'] = PIPE
    proc = yield from asyncio.create_subprocess_shell(cmd, **kwds)

    tasks = []
    if input is not None:
        tasks.append(send_input(proc.stdin, input))
    else:
        print('No stdin')
    if proc.stderr is not None:
        tasks.append(log_errors(proc.stderr))
    else:
        print('No stderr')
    if proc.stdout is not None:
        tasks.append(read_stdout(proc.stdout))
    else:
        print('No stdout')

    if tasks:
        # feed stdin while consuming stdout to avoid hang
        # when stdin pipe is full
        yield from asyncio.wait(tasks)

    exitcode = yield from proc.wait()
    print("exit code: %s" % exitcode)


def main():
    if os.name == 'nt':
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()
    loop.run_until_complete(start(
        'sleep 2; wc', input=[b'foo bar baz\n'*300 for i in range(100)]))
    loop.close()


if __name__ == '__main__':
    main()
