import os
import sys

asyncio_path = os.path.join(os.path.dirname(__file__), '..')
asyncio_path = os.path.abspath(asyncio_path)

sys.path.insert(0, asyncio_path)
from trollius.py33_exceptions import wrap_error
sys.path.remove(asyncio_path)

if __name__ == '__main__':
    while True:
        buf = os.read(0, 1024)
        if not buf:
            break
        try:
            wrap_error(os.write, 1, b'OUT:'+buf)
        except OSError as ex:
            os.write(2, b'ERR:' + ex.__class__.__name__.encode('ascii'))
