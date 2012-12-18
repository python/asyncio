"""Crummy HTTP client.

This is not meant as an example of how to write a good client.
"""

# Stdlib.
import re
import time

# Local.
import sockets


def urlfetch(host, port=None, path='/', method='GET',
             body=None, hdrs=None, encoding='utf-8', ssl=None, af=0):
    """COROUTINE: Make an HTTP 1.0 request."""
    t0 = time.time()
    if port is None:
        if ssl:
            port = 443
        else:
            port = 80
    trans = yield from sockets.create_transport(host, port, ssl=ssl, af=af)
    yield from trans.send(method.encode(encoding) + b' ' +
                          path.encode(encoding) + b' HTTP/1.0\r\n')
    if hdrs:
        kwds = dict(hdrs)
    else:
        kwds = {}
    if 'host' not in kwds:
        kwds['host'] = host
    if body is not None:
        kwds['content_length'] = len(body)
    for header, value in kwds.items():
        yield from trans.send(header.replace('_', '-').encode(encoding) +
                              b': ' + value.encode(encoding) + b'\r\n')

    yield from trans.send(b'\r\n')
    if body is not None:
        yield from trans.send(body)

    # Read HTTP response line.
    rdr = sockets.BufferedReader(trans)
    resp = yield from rdr.readline()
    m = re.match(br'(?ix) http/(\d\.\d) \s+ (\d\d\d) \s+ ([^\r]*)\r?\n\Z',
                 resp)
    if not m:
        trans.close()
        raise IOError('No valid HTTP response: %r' % resp)
    http_version, status, message = m.groups()

    # Read HTTP headers.
    headers = []
    hdict = {}
    while True:
        line = yield from rdr.readline()
        if not line.strip():
            break
        m = re.match(br'([^\s:]+):\s*([^\r]*)\r?\n\Z', line)
        if not m:
            raise IOError('Invalid header: %r' % line)
        header, value = m.groups()
        headers.append((header, value))
        hdict[header.decode(encoding).lower()] = value.decode(encoding)

    # Read response body.
    content_length = hdict.get('content-length')
    if content_length is not None:
        size = int(content_length)  # TODO: Catch errors.
        assert size >= 0, size
    else:
        size = 2**20  # Protective limit (1 MB).
    data = yield from rdr.readexactly(size)
    trans.close()  # Can this block?
    t1 = time.time()
    result = (host, port, path, int(status), len(data), round(t1-t0, 3))
##     print(result)
    return result
