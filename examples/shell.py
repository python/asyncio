"""Examples using create_subprocess_exec() and create_subprocess_shell()."""

import asyncio
import signal
import subprocess

@asyncio.coroutine
def cat(loop):
    proc = yield from asyncio.create_subprocess_shell("cat",
                                                      stdin=subprocess.PIPE,
                                                      stdout=subprocess.PIPE)
    print("pid: %s" % proc.pid)

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
    proc = yield from asyncio.create_subprocess_exec("ls",
                                                     stdout=subprocess.PIPE)
    while True:
        line = yield from proc.stdout.readline()
        if not line:
            break
        print("ls>>", line.decode('ascii').rstrip())
    try:
        proc.send_signal(signal.SIGINT)
    except ProcessLookupError:
        pass
    proc.close()

@asyncio.coroutine
def call(*args, timeout=None):
    proc = yield from asyncio.create_subprocess_exec(*args)
    try:
        exitcode = yield from asyncio.wait_for(proc.wait(), timeout)
        print("%s: exit code %s" % (' '.join(args), exitcode))
    except asyncio.TimeoutError:
        print("timeout! (%.1f sec)" % timeout)
    finally:
        proc.close()

loop = asyncio.get_event_loop()
loop.run_until_complete(cat(loop))
loop.run_until_complete(ls(loop))
loop.run_until_complete(call("bash", "-c", "sleep 3", timeout=1.0))
