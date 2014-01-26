import asyncio

@asyncio.coroutine
def cat(loop):
    transport, proc = yield from loop.subprocess_shell(asyncio.SubprocessStreamProtocol, "cat")
    print("pid: %s" % transport.get_pid())

    message = "Hello World!"
    print("cat write: %r" % message)
    proc.stdin.write(message.encode('ascii'))
    yield from proc.stdin.drain()

    proc.stdin.close()
    read = yield from proc.stdout.read()
    print("cat read: %r" % read.decode('ascii'))

    returncode = yield from proc.wait()
    print("exit code: %s" % returncode)
    transport.close()

@asyncio.coroutine
def ls(loop):
    transport, proc = yield from loop.subprocess_exec(asyncio.SubprocessStreamProtocol, "ls", stdin=None)
    while True:
        line = yield from proc.stdout.readline()
        if not line:
            break
        print("ls>>", line.decode('ascii').rstrip())
    transport.close()

loop = asyncio.get_event_loop()
loop.run_until_complete(cat(loop))
loop.run_until_complete(ls(loop))
