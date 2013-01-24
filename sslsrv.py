"""Serve up an SSL connection, after Python ssl module docs."""

import socket
import ssl
import os


def main():
    context = ssl.SSLContext(ssl.PROTOCOL_TLSv1)
    certfile = getcertfile()
    context.load_cert_chain(certfile=certfile, keyfile=certfile)
    bindsocket = socket.socket()
    bindsocket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    bindsocket.bind(('', 4443))
    bindsocket.listen(5)

    while True:
        newsocket, fromaddr = bindsocket.accept()
        try:
            connstream = context.wrap_socket(newsocket, server_side=True)
            try:
                deal_with_client(connstream)
            finally:
                connstream.shutdown(socket.SHUT_RDWR)
                connstream.close()
        except Exception as exc:
            print(exc.__class__.__name__ + ':', exc)


def getcertfile():
    import test  # Test package
    testdir = os.path.dirname(test.__file__)
    certfile = os.path.join(testdir, 'keycert.pem')
    print('certfile =', certfile)
    return certfile


def deal_with_client(connstream):
    data = connstream.recv(1024)
    # empty data means the client is finished with us
    while data:
        if not do_something(connstream, data):
            # we'll assume do_something returns False
            # when we're finished with client
            break
        data = connstream.recv(1024)
    # finished with client


def do_something(connstream, data):
    # just echo back
    connstream.sendall(data)


if __name__ == '__main__':
    main()
