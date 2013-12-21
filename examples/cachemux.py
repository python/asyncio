import asyncio
import json
import logging


class Mux:
    """Demultiplexer for requests and responses.

    This also handles retries.
    """

    def __init__(self, host, port, *, sslctx=None, loop=None):
        self.host = host
        self.port = port
        self.sslctx = sslctx
        self.loop = loop
        self.todo = set()
        self.initialized = False
        asyncio.Task(self.activity())

    @asyncio.coroutine
    def request(self, type, key, value=None):
        data = {'type': type, 'key': key}
        if value is not None:
            data['value'] = value
        payload = json.dumps(data).encode('utf8')
        waiter = asyncio.Future(loop=self.loop)
        if self.initialized:
            yield from self.send(payload, waiter)
        else:
            self.todo.add((payload, waiter))
        return (yield from waiter)

    @asyncio.coroutine
    def activity(self):
        while True:
            try:
                self.reader, self.writer = yield from asyncio.open_connection(
                    self.host, self.post, ssl=loop.sslctx, loop=self.loop)
            except IOError as exc:
                logging.info('I/O error connecting: %r', exc)
                yield from asyncio.sleep(1, loop=self.loop)
                continue
            self.next_id = 0
            self.pending = {}
            self. initialized = True
            try:
                while self.todo:
                    payload, waiter = self.todo.pop()
                    yield from self.send(payload, waiter)
                while True:
                    resp_id, resp = self.process()
                    if resp_id in self.pending:
                        payload, waiter = self.pending.pop(resp_id)
                        if not waiter.done():
                            waiter.set_result(resp)
            except IOError as exc:
                self.initialized = False
                self.writer.close()
                while self.pending:
                    req_id, (payload, waiter) = self.pending.popitem()
                    if not waiter.done():
                        self.todo.add(pair)
                logging.info('I/O error processing: %r', exc)

    @asyncio.coroutine
    def send(self, payload, waiter):
        self.next_id += 1
        req_id = self.next_id
        frame = 'request %d %d\n' % (req_id, len(payload))
        self.writer.write(frame.encode('ascii'))
        self.writer.write(payload)
        self.pending[req_id] = payload, waiter
        yield from self.writer.drain()

    @asyncio.coroutine
    def process(self):
        frame = yield from self.reader.readline()
        if not frame:
            raise IOError('EOF')
        head, tail = frame.split(None, 1)
        if head == b'error':
            raise IOError('OOB error: %r' % tail)
        if head != b'response':
            raise IOError('Bad frame: %r' % frame)
        resp_id, resp_size = map(int, tail.split())
        data = yield from self.reader.readexactly(resp_size)
        resp = json.loads(data.decode('utf8'))
        return resp_id, resp
