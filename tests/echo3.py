import os
from trollius.py33_exceptions import wrap_error

if __name__ == '__main__':
    while True:
        buf = os.read(0, 1024)
        if not buf:
            break
        try:
            wrap_error(os.write, 1, b'OUT:'+buf)
        except OSError as ex:
            os.write(2, b'ERR:' + ex.__class__.__name__.encode('ascii'))
