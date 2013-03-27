#!/usr/bin/env python3.3
"""Minimal synchronous SSL demo, connecting to xkcd.com."""

import socket, ssl

s = socket.socket()
s.connect(('xkcd.com', 443))
ss = ssl.wrap_socket(s)

ss.send(b'GET / HTTP/1.0\r\n\r\n')

while True:
    data = ss.recv(1000000)
    print(data)
    if not data:
        break

ss.close()
