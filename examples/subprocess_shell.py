"""Example writing to *and* reading from a subprocess."""

import asyncio
import subprocess


@asyncio.coroutine
def send_input(writer, input):
    for line in input:
        print('sending', len(line), 'bytes')
        writer.write(line)
        d = writer.drain()
        if d:
            print('pause writing')
            yield from d
            print('resume writing')
    writer.close()

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
    if input is None and 'stdin' not in kwds:
        kwds['stdin'] = None
    transport, proc = yield from asyncio.subprocess_shell(cmd, **kwds)

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
    # feed stdin while consuming stdout to avoid hang when stdin pipe is full
    yield from asyncio.wait(tasks)

    exitcode = yield from proc.wait()
    print("exit code: %s" % exitcode)


def main():
    loop = asyncio.get_event_loop()
##     print('-'*20)
##     loop.run_until_complete(start('cat', [b'one\n', b'two\n', b'three\n']))
##     print('-'*20)
##     loop.run_until_complete(start('cat 1>&2', [b'three\n', b'four\n']))
##     print('-'*20)
##     loop.run_until_complete(start('echo Foo'))
##     print('-'*20)
##     loop.run_until_complete(start('echo Foo; echo Bar 1>&2'))
##     print('-'*20)
##     loop.run_until_complete(start('echo Foo; echo Bar 1>&2', stderr=subprocess.STDOUT))
##     print('-'*20)
##     loop.run_until_complete(start('echo Foo; echo Bar 1>&2', stderr=None))
##     print('-'*20)
##     loop.run_until_complete(start('echo Foo; echo Bar 1>&2', stdout=None))
    print('-'*20)
    loop.run_until_complete(start('sleep 2; wc', input=[b'foo bar baz\n'*300 for i in range(100)]))


if __name__ == '__main__':
    main()
