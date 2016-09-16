import select
import socket

class T:
    _select = select.select


t = T()
s = socket.socket()
t._select([], [s.fileno()], [], 1.0)