import trollius as asyncio
from trollius import From

@asyncio.coroutine
def echo_server():
    yield From(asyncio.start_server(handle_connection, 'localhost', 8000))

@asyncio.coroutine
def handle_connection(reader, writer):
    while True:
        data = yield From(reader.read(8192))
        if not data:
            break
        writer.write(data)

loop = asyncio.get_event_loop()
loop.run_until_complete(echo_server())
try:
    loop.run_forever()
finally:
    loop.close()
