"""websocket cmd client for wssrv.py example."""
import base64
import hashlib
import os
import signal
import sys

import tulip
import tulip.http
from tulip.http import websocket

WS_KEY = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def start_client(loop):
    name = input('Please enter your name: ').encode()

    url = 'http://localhost:8080/'
    sec_key = base64.b64encode(os.urandom(16))

    # send request
    response = yield from tulip.http.request(
        'get', url,
        headers={
            'UPGRADE': 'WebSocket',
            'CONNECTION': 'Upgrade',
            'SEC-WEBSOCKET-VERSION': '13',
            'SEC-WEBSOCKET-KEY': sec_key.decode(),
        }, timeout=1.0)

    # websocket handshake
    if response.status != 101:
        raise ValueError("Handshake error: Invalid response status")
    if response.get('upgrade', '').lower() != 'websocket':
        raise ValueError("Handshake error - Invalid upgrade header")
    if response.get('connection', '').lower() != 'upgrade':
        raise ValueError("Handshake error - Invalid connection header")

    key = response.get('sec-websocket-accept', '').encode()
    match = base64.b64encode(hashlib.sha1(sec_key + WS_KEY).digest())
    if key != match:
        raise ValueError("Handshake error - Invalid challenge response")

    # switch to websocket protocol
    stream = response.stream.set_parser(websocket.websocket_parser())
    writer = websocket.websocket_writer(response.transport)

    # input reader
    loop.add_reader(
        sys.stdin.fileno(),
        lambda: writer.send(name + b': ' + sys.stdin.readline().encode()))

    @tulip.coroutine
    def dispatch():
        while True:
            msg = yield from stream.read()
            if msg is None:
                break
            elif msg.opcode == websocket.OPCODE_PING:
                writer.pong()
            elif msg.opcode == websocket.OPCODE_TEXT:
                print(msg.data.strip())
            elif msg.opcode == websocket.OPCODE_CLOSE:
                break

    yield from dispatch()


if __name__ == '__main__':
    loop = tulip.get_event_loop()
    loop.add_signal_handler(signal.SIGINT, loop.stop)
    loop.run_until_complete(start_client(loop))
