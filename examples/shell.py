# Test script for the subproces-stream branch of Tulip

import asyncio

@asyncio.coroutine
def cat(loop):
    transport, proc = yield from asyncio.run_shell("cat")
    print("pid: %s" % transport.get_pid())

    message = "Hello World!"
    print("cat write: %r" % message)
    proc.stdin.write(message.encode('ascii'))
    yield from proc.stdin.drain()

    proc.stdin.close()
    read = yield from proc.stdout.read()
    print("cat read: %r" % read.decode('ascii'))

    exitcode = yield from proc.wait()
    print("(exit code %s)" % exitcode)

@asyncio.coroutine
def ls(loop):
    transport, proc = yield from asyncio.run_program("ls", stdin=None)
    while True:
        line = yield from proc.stdout.readline()
        if not line:
            break
        print("ls>>", line.decode('ascii').rstrip())
    transport.close()

@asyncio.coroutine
def call(*args, timeout=None):
    transport, proc = yield from asyncio.run_program(*args, stdin=None, stdout=None, stderr=None)
    try:
        task = proc.wait()
        if timeout is not None:
            returncode = yield from asyncio.wait_for(task, timeout=timeout)
        else:
            returncode = yield from task
        return returncode
    except asyncio.TimeoutError:
        print("timeout! (%.1f sec)" % timeout)
        transport.close()

loop = asyncio.get_event_loop()
loop.run_until_complete(cat(loop))
loop.run_until_complete(ls(loop))
loop.run_until_complete(call("sync", timeout=1.0))
