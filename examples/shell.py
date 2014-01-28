# Test script for the subproces-stream branch of Tulip

import asyncio
import signal
import subprocess

@asyncio.coroutine
def cat(loop):
    proc = yield from asyncio.run_shell("cat",
                                        stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE)
    # test get_pid()
    print("pid: %s" % proc.get_pid())

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
    proc = yield from asyncio.run_program("ls", stdout=subprocess.PIPE)
    while True:
        line = yield from proc.stdout.readline()
        if not line:
            break
        print("ls>>", line.decode('ascii').rstrip())
    # use the Popen object
    try:
        proc.get_subprocess().send_signal(signal.SIGINT)
    except ProcessLookupError:
        pass
    proc.close()

@asyncio.coroutine
def call(*args, timeout=None):
    proc = yield from asyncio.run_program(*args)
    try:
        task = proc.wait()
        if timeout is not None:
            returncode = yield from asyncio.wait_for(task, timeout=timeout)
        else:
            returncode = yield from task
        return returncode
    except asyncio.TimeoutError:
        print("timeout! (%.1f sec)" % timeout)
        proc.close()

loop = asyncio.get_event_loop()
loop.run_until_complete(cat(loop))
loop.run_until_complete(ls(loop))
loop.run_until_complete(call("sync", timeout=1.0))
