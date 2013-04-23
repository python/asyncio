"""A socket pair usable as a self-pipe, for Windows.

Origin: https://gist.github.com/4325783, by Geert Jansen.  Public domain.
"""

import socket
import sys

if sys.platform != 'win32':  # pragma: no cover
    raise ImportError('winsocketpair is win32 only')


def socketpair(family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0):
    """Emulate the Unix socketpair() function on Windows."""
    # We create a connected TCP socket. Note the trick with setblocking(0)
    # that prevents us from having to create a thread.
    lsock = socket.socket(family, type, proto)
    lsock.bind(('localhost', 0))
    lsock.listen(1)
    addr, port = lsock.getsockname()
    csock = socket.socket(family, type, proto)
    csock.setblocking(False)
    try:
        csock.connect((addr, port))
    except (BlockingIOError, InterruptedError):
        pass
    except Exception:
        lsock.close()
        csock.close()
        raise
    ssock, _ = lsock.accept()
    csock.setblocking(True)
    lsock.close()
    return (ssock, csock)
