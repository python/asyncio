#!/usr/bin/env python3.4

"""A simple web crawler."""

# TODO:
# - Less verbose logging.
# - Support gzip encoding.
# - Close connection if HTTP/1.0 response.
# - Add timeouts.
# - Improve class structure (e.g. add a Connection class).
# - Skip reading large non-text/html files?
# - Use ETag and If-Modified-Since?

import argparse
import asyncio
import asyncio.locks
import cgi
from http.client import BadStatusLine
import logging
import re
import signal
import sys
import time
import urllib.parse


ARGS = argparse.ArgumentParser(description="Web crawler")
ARGS.add_argument(
    '--iocp', action='store_true', dest='iocp',
    default=False, help='Use IOCP event loop (Windows only)')
ARGS.add_argument(
    '--select', action='store_true', dest='select',
    default=False, help='Use Select event loop instead of default')
ARGS.add_argument(
    'roots', nargs='*',
    default=[], help='Root URL (may be repeated)')
ARGS.add_argument(
    '--max_redirect', action='store', type=int, metavar='N',
    default=10, help='Limit redirection chains (for 301, 302 etc.)')
ARGS.add_argument(
    '--max_tries', action='store', type=int, metavar='N',
    default=4, help='Limit retries on network errors')
ARGS.add_argument(
    '--max_tasks', action='store', type=int, metavar='N',
    default=5, help='Limit concurrent connections')
ARGS.add_argument(
    '--max_pool', action='store', type=int, metavar='N',
    default=10, help='Limit connection pool size')
ARGS.add_argument(
    '--exclude', action='store', metavar='REGEX',
    help='Exclude matching URLs')
ARGS.add_argument(
    '--strict', action='store_true',
    default=True, help='Strict host matching (default)')
ARGS.add_argument(
    '--lenient', action='store_false', dest='strict',
    default=False, help='Lenient host matching')

ARGS.add_argument(
    '-v', '--verbose', action='count', dest='verbose',
    default=1, help='Verbose logging (repeat for more verbose)')
ARGS.add_argument(
    '-q', '--quiet', action='store_const', const=0, dest='verbose',
    help='Quiet logging (opposite of --verbose)')


ESCAPES = [('quot', '"'),
           ('gt', '>'),
           ('lt', '<'),
           ('amp', '&')  # Must be last.
           ]

def unescape(url):
    """Turn &amp; into &, and so on.

    This is the inverse of cgi.escape().
    """
    for name, char in ESCAPES:
        url = url.replace('&' + name + ';', char)
    return url


def fix_url(url):
    """Prefix a schema-less URL with http://."""
    if '://' not in url:
        url = 'http://' + url
    return url


class VPrinter:
    """Mix-in class defining vprint() which is like print() if verbose > 0.

    The output goes to stderr.  Only positional arguments are
    supported.  There are also methods vvprint(), vvvprint()
    etc. which print() only if verbose > larger values.

    The verbose instance variable is public.

    TODO: This should probably be a shared object rather than a mix-in class.
    """

    def __init__(self, verbose):
        self.verbose = verbose

    def _nvprint(self, n, args):
        if self.verbose >= n:
            print(*args, file=sys.stderr, flush=True)

    def nvprint(self, n, *args):
        self._nvprint(n, args)

    def vprint(self, *args):
        self._nvprint(1, args)

    def vvprint(self, *args):
        self._nvprint(2, args)

    def vvvprint(self, *args):
        self._nvprint(3, args)


class ConnectionPool(VPrinter):
    """A connection pool.

    To open a connection, use reserve().  To recycle it, use unreserve().

    The pool is mostly just a mapping from (host, port, ssl) tuples to
    lists of (reader, writer) pairs.  The currently active connections
    are *not* in the data structure; reserve() takes the connection
    out, and unreserve()' puts it back in.  It is up to the caller to
    only call unreserve() for reusable connections.  (That logic is
    implemented in the Request class.)

    There are limits to both the overal pool and the per-key pool.
    """

    def __init__(self, max_pool=10, max_tasks=5, verbose=0):
        VPrinter.__init__(self, verbose)
        self.max_pool = max_pool  # Overall limit.
        self.max_tasks = max_tasks  # Per-key limit.
        self.loop = asyncio.get_event_loop()
        self.connections = {}  # {(host, port, ssl): [(reader, writer)]}
        self.queue = []  # [(key, pair)]

    def close(self):
        """Close all connections available for reuse."""
        for pairs in self.connections.values():
            for _, writer in pairs:
                writer.close()
        self.connections.clear()
        self.queue.clear()

    @asyncio.coroutine
    def reserve(self, host, port, ssl):
        """Create or reuse a connection."""
        port = port or (443 if ssl else 80)
        try:
            ipaddrs = yield from self.loop.getaddrinfo(host, port)
        except Exception as exc:
            self.vprint('Exception %r for (%r, %r)' % (exc, host, port))
            raise
        self.vprint('* %s resolves to %s' %
                    (host, ', '.join(ip[4][0] for ip in ipaddrs)))

        # Look for a reusable connection.
        for _, _, _, _, (h, p, *_) in ipaddrs:
            key = h, p, ssl
            pair = None
            pairs = self.connections.get(key)
            while pairs:
                pair = pairs.pop(0)
                self.queue.remove((key, pair))
                if not pairs:
                    del self.connections[key]
                reader, writer = pair
                if reader._eof:
                    self.vprint('(cached connection closed for %s)' %
                                repr(key))
                    writer.close()  # Just in case.
                else:
                    self.vprint('* Reusing pooled connection', key,
                                'FD =', writer._transport._sock.fileno())
                    return key, reader, writer

        # Create a new connection.
        reader, writer = yield from asyncio.open_connection(host, port,
                                                            ssl=ssl)
        peername = writer.get_extra_info('peername')
        if peername:
            host, port, *_ = peername
        else:
            self.vprint('NO PEERNAME???', host, port, ssl)
        key = host, port, ssl
        self.vprint('* New connection', key,
                    'FD =', writer._transport._sock.fileno())
        return key, reader, writer

    def unreserve(self, key, reader, writer):
        """Make a connection available for reuse.

        This also prunes the pool if it exceeds the size limits.
        """
        if reader._eof:
            writer.close()
            return
        pair = reader, writer
        pairs = self.connections.setdefault(key, [])
        pairs.append(pair)
        self.queue.append((key, pair))

        # Close oldest connection(s) for this key if limit reached.
        while len(pairs) > self.max_tasks:
            pair = pairs.pop(0)
            self.vprint('closing oldest connection for', key)
            self.queue.remove((key, pair))
            reader, writer = pair
            writer.close()

        # Close oldest overall connection(s) if limit reached.
        while len(self.queue) > self.max_pool:
            key, pair = self.queue.pop(0)
            self.vprint('closing olderst connection', key)
            pairs = self.connections.get(key)
            p = pairs.pop(0)
            assert pair == p, (key, pair, p, pairs)
            reader, writer = pair
            writer.close()


class Request(VPrinter):
    """HTTP request.

    Use connect() to open a connection; send_request() to send the
    request; get_response() to receive the response headers.
    """

    def __init__(self, url, pool, verbose=0):
        VPrinter.__init__(self, verbose)
        self.url = url
        self.pool = pool
        self.parts = urllib.parse.urlparse(self.url)
        self.scheme = self.parts.scheme
        assert self.scheme in ('http', 'https'), repr(url)
        self.ssl = self.parts.scheme == 'https'
        self.netloc = self.parts.netloc
        self.hostname = self.parts.hostname
        self.port = self.parts.port or (443 if self.ssl else 80)
        self.path = (self.parts.path or '/')
        self.query = self.parts.query
        if self.query:
            self.full_path = '%s?%s' % (self.path, self.query)
        else:
            self.full_path = self.path
        self.http_version = 'HTTP/1.1'
        self.method = 'GET'
        self.headers = []
        self.key = None
        self.reader = None
        self.writer = None

    @asyncio.coroutine
    def connect(self):
        """Open a connection to the server."""
        self.vprint('* Connecting to %s:%s using %s for %s' %
                    (self.hostname, self.port,
                     'ssl' if self.ssl else 'tcp',
                     self.url))
        self.key, self.reader, self.writer = \
            yield from self.pool.reserve(self.hostname, self.port, self.ssl)
        self.vprint('* Connected to %s' %
                    (self.writer.get_extra_info('peername'),))

    def recycle_connection(self):
        """Recycle the connection to the pool.

        This should only be called when a properly formatted HTTP
        response has been received.
        """
        self.pool.unreserve(self.key, self.reader, self.writer)
        self.key = self.reader = self.writer = None

    def close(self):
        if self.writer is not None:
            self.writer.close()
        self.key = self.reader = self.writer = None

    @asyncio.coroutine
    def putline(self, line):
        """Write a line to the connection.

        Used for the request line and headers.
        """
        self.vvprint('>', line)
        self.writer.write(line.encode('latin-1') + b'\r\n')

    @asyncio.coroutine
    def send_request(self):
        """Send the request."""
        request_line = '%s %s %s' % (self.method, self.full_path,
                                     self.http_version)
        yield from self.putline(request_line)
        # TODO: What if a header is already set?
        self.headers.append(('User-Agent', 'asyncio-example-crawl/0.0'))
        self.headers.append(('Host', self.netloc))
        self.headers.append(('Accept', '*/*'))
        ##self.headers.append(('Accept-Encoding', 'gzip'))
        for key, value in self.headers:
            line = '%s: %s' % (key, value)
            yield from self.putline(line)
        yield from self.putline('')

    @asyncio.coroutine
    def get_response(self):
        """Receive the response."""
        response = Response(self.reader, self.verbose)
        yield from response.read_headers()
        return response


class Response(VPrinter):
    """HTTP response.

    Call read_headers() to receive the request headers.  Then check
    the status attribute and call get_header() to inspect the headers.
    Finally call read() to receive the body.
    """

    def __init__(self, reader, verbose=0):
        VPrinter.__init__(self, verbose)
        self.reader = reader
        self.http_version = None  # 'HTTP/1.1'
        self.status = None  # 200
        self.reason = None  # 'Ok'
        self.headers = []  # [('Content-Type', 'text/html')]

    @asyncio.coroutine
    def getline(self):
        """Read one line from the connection."""
        line = (yield from self.reader.readline()).decode('latin-1').rstrip()
        self.vvprint('<', line)
        return line

    @asyncio.coroutine
    def read_headers(self):
        """Read the response status and the request headers."""
        status_line = yield from self.getline()
        status_parts = status_line.split(None, 2)
        if len(status_parts) != 3:
            self.vprint('bad status_line', repr(status_line))
            raise BadStatusLine(status_line)
        self.http_version, status, self.reason = status_parts
        self.status = int(status)
        while True:
            header_line = yield from self.getline()
            if not header_line:
                break
            # TODO: Continuation lines.
            key, value = header_line.split(':', 1)
            self.headers.append((key, value.strip()))

    def get_redirect_url(self, default=''):
        """Inspect the status and return the redirect url if appropriate."""
        if self.status not in (300, 301, 302, 303, 307):
            return default
        return self.get_header('Location', default)

    def get_header(self, key, default=''):
        """Get one header value, using a case insensitive header name."""
        key = key.lower()
        for k, v in self.headers:
            if k.lower() == key:
                return v
        return default

    @asyncio.coroutine
    def readexactly(self, nbytes):
        """Wrapper for readexactly() that raise EOFError if not enough data.

        This also logs (at the vvv level) while it is reading.
        """
        blocks = []
        nread = 0
        while nread < nbytes:
            self.vvvprint('reading block', len(blocks),
                          'with', nbytes - nread, 'bytes remaining')
            block = yield from self.reader.read(nbytes-nread)
            self.vvvprint('read', len(block), 'bytes')
            if not block:
                raise EOFError('EOF with %d more bytes expected' %
                               (nbytes - nread))
            blocks.append(block)
            nread += len(block)
        return b''.join(blocks)

    @asyncio.coroutine
    def read(self):
        """Read the response body.

        This honors Content-Length and Transfer-Encoding: chunked.
        """
        nbytes = None
        for key, value in self.headers:
            if key.lower() == 'content-length':
                nbytes = int(value)
                break
        if nbytes is None:
            if self.get_header('transfer-encoding').lower() == 'chunked':
                self.vvprint('parsing chunked response')
                blocks = []
                while True:
                    size_header = yield from self.reader.readline()
                    if not size_header:
                        self.vprint('premature end of chunked response')
                        break
                    self.vvvprint('size_header =', repr(size_header))
                    parts = size_header.split(b';')
                    size = int(parts[0], 16)
                    if size:
                        self.vvvprint('reading chunk of', size, 'bytes')
                        block = yield from self.readexactly(size)
                        assert len(block) == size, (len(block), size)
                        blocks.append(block)
                    crlf = yield from self.reader.readline()
                    assert crlf == b'\r\n', repr(crlf)
                    if not size:
                        break
                body = b''.join(blocks)
                self.vprint('chunked response had',len(body),
                            'bytes in', len(blocks), 'blocks')
            else:
                self.vvvprint('reading until EOF')
                body = yield from self.reader.read()
                # TODO: Should make sure not to recycle the connection
                # in this case.
        else:
            body = yield from self.readexactly(nbytes)
        return body


class Fetcher(VPrinter):
    """Logic and state for one URL.

    When found in crawler.busy, this represents a URL to be fetched or
    in the process of being fetched; when found in crawler.done, this
    holds the results from fetching it.

    This is usually associated with a task.  This references the
    crawler for the connection pool and to add more URLs to its todo
    list.

    Call fetch() to do the fetching, then report() to print the results.
    """

    def __init__(self, url, crawler, max_redirect=10, max_tries=4, verbose=0):
        VPrinter.__init__(self, verbose)
        self.url = url
        self.crawler = crawler
        # We don't loop resolving redirects here -- we just use this
        # to decide whether to add the redirect URL to crawler.todo.
        self.max_redirect = max_redirect
        # But we do loop to retry on errors a few times.
        self.max_tries = max_tries
        # Everything we collect from the response goes here.
        self.task = None
        self.exceptions = []
        self.tries = 0
        self.request = None
        self.response = None
        self.body = None
        self.next_url = None
        self.ctype = None
        self.pdict = None
        self.encoding = None
        self.urls = None
        self.new_urls = None

    @asyncio.coroutine
    def fetch(self):
        """Attempt to fetch the contents of the URL.

        If successful, and the data is HTML, extract further links and
        add them to the crawler.  Redirects are also added back there.
        """
        while self.tries < self.max_tries:
            self.tries += 1
            self.request = None
            try:
                self.request = Request(self.url, self.crawler.pool,
                                       self.verbose)
                yield from self.request.connect()
                yield from self.request.send_request()
                self.response = yield from self.request.get_response()
                self.body = yield from self.response.read()
                h_conn = self.response.get_header('connection').lower()
                h_t_enc = self.response.get_header('transfer-encoding').lower()
                if h_conn != 'close':
                    self.request.recycle_connection()
                    self.request = None
                if self.tries > 1:
                    self.vprint('try', self.tries, 'for', self.url, 'success')
                break
            except (BadStatusLine, OSError) as exc:
                self.exceptions.append(exc)
                self.vprint('try', self.tries, 'for', self.url,
                            'raised', repr(exc))
                ##import pdb; pdb.set_trace()
                # Don't reuse the connection in this case.
            finally:
                if self.request is not None:
                    self.request.close()
        else:
            # We never broke out of the while loop, i.e. all tries failed.
            self.vprint('no success for', self.url,
                        'in', self.max_tries, 'tries')
            return
        next_url = self.response.get_redirect_url()
        if next_url:
            self.next_url = urllib.parse.urljoin(self.url, next_url)
            if self.max_redirect > 0:
                self.vprint('redirect to', self.next_url, 'from', self.url)
                self.crawler.add_url(self.next_url, self.max_redirect-1)
            else:
                self.vprint('redirect limit reached for', self.next_url,
                            'from', self.url)
        else:
            if self.response.status == 200:
                self.ctype = self.response.get_header('content-type')
                self.pdict = {}
                if self.ctype:
                    self.ctype, self.pdict = cgi.parse_header(self.ctype)
                self.encoding = self.pdict.get('charset', 'utf-8')
                if self.ctype == 'text/html':
                    body = self.body.decode(self.encoding, 'replace')
                    self.urls = set(re.findall(r'(?i)href=["\']?([^\s"\'<>]+)',
                                               body))
                    if self.urls:
                        self.vprint('got', len(self.urls),
                                    'distinct urls from', self.url)
                    self.new_urls = set()
                    for url in self.urls:
                        url = unescape(url)
                        url = urllib.parse.urljoin(self.url, url)
                        url, frag = urllib.parse.urldefrag(url)
                        if self.crawler.add_url(url):
                            self.new_urls.add(url)

    def report(self, file=None):
        """Print a report on the state for this URL."""
        if self.task is not None:
            if not self.task.done():
                print(self.url, 'pending', file=file)
                return
            elif self.task.cancelled():
                print(self.url, 'cancelled', file=file)
                return
            elif self.task.exception():
                print(self.url, self.task.exception(), file=file)
                return
        if len(self.exceptions) == self.tries:
            print(self.url, 'error', self.exceptions[-1], file=file)
        elif self.next_url:
            print(self.url, self.response.status, 'redirect', self.next_url,
                  file=file)
        elif self.ctype == 'text/html':
            print(self.url, self.response.status,
                  self.ctype, self.encoding,
                  len(self.body or b''),
                  '%d/%d' % (len(self.new_urls or ()), len(self.urls or ())),
                  file=file)
        else:
            print(self.url, self.response.status,
                  self.ctype, self.encoding,
                  len(self.body or b''), self.ctype,
                  file=file)


class Crawler(VPrinter):
    """Crawl a set of URLs.

    This manages three disjoint sets of URLs (todo, busy, done).  The
    data structures actually store dicts -- the values in todo give
    the redirect limit, while the values in busy and done are Fetcher
    instances.
    """
    def __init__(self,
                 roots, exclude=None, strict=True,  # What to crawl.
                 max_redirect=10, max_tries=4,  # Per-url limits.
                 max_tasks=10, max_pool=10,  # Global limits.
                 verbose=0):
        VPrinter.__init__(self, verbose)
        self.roots = roots
        self.exclude = exclude
        self.strict = strict
        self.max_redirect = max_redirect
        self.max_tries = max_tries
        self.max_tasks = max_tasks
        self.max_pool = max_pool
        self.todo = {}
        self.busy = {}
        self.done = {}
        self.pool = ConnectionPool(max_pool, max_tasks, self.verbose)
        self.root_domains = set()
        for root in roots:
            parts = urllib.parse.urlparse(root)
            host, port = urllib.parse.splitport(parts.netloc)
            if not host:
                continue
            if re.match(r'\A[\d\.]*\Z', host):
                self.root_domains.add(host)
            else:
                host = host.lower()
                if self.strict:
                    self.root_domains.add(host)
                    if host.startswith('www.'):
                        self.root_domains.add(host[4:])
                    else:
                        self.root_domains.add('www.' + host)
                else:
                    parts = host.split('.')
                    if len(parts) > 2:
                        host = '.'.join(parts[-2:])
                    self.root_domains.add(host)
        for root in roots:
            self.add_url(root)
        self.governor = asyncio.locks.Semaphore(max_tasks)
        self.termination = asyncio.locks.Condition()
        self.t0 = time.time()
        self.t1 = None

    def close(self):
        """Close resources (currently only the pool)."""
        self.pool.close()

    def host_okay(self, host):
        """Check if a host should be crawled.

        A literal match (after lowercasing) is always good.  For hosts
        that don't look like IP addresses, some approximate matches
        are okay depending on the strict flag.
        """
        host = host.lower()
        if host in self.root_domains:
            return True
        if re.match(r'\A[\d\.]*\Z', host):
            return False
        if self.strict:
            return self._host_okay_strictish(host)
        else:
            return self._host_okay_lenient(host)

    def _host_okay_strictish(self, host):
        """Check if a host should be crawled, strict-ish version.

        This checks for equality modulo an initial 'www.' component.
         """
        if host.startswith('www.'):
            if host[4:] in self.root_domains:
                return True
        else:
            if 'www.' + host in self.root_domains:
                return True
        return False

    def _host_okay_lenient(self, host):
        """Check if a host should be crawled, lenient version.

        This compares the last two components of the host.
        """
        parts = host.split('.')
        if len(parts) > 2:
            host = '.'.join(parts[-2:])
        return host in self.root_domains

    def add_url(self, url, max_redirect=None):
        """Add a URL to the todo list if not seen before."""
        if self.exclude and re.search(self.exclude, url):
            return False
        parts = urllib.parse.urlparse(url)
        if parts.scheme not in ('http', 'https'):
            self.vvprint('skipping non-http scheme in', url)
            return False
        host, port = urllib.parse.splitport(parts.netloc)
        if not self.host_okay(host):
            self.vvprint('skipping non-root host in', url)
            return False
        if max_redirect is None:
            max_redirect = self.max_redirect
        if url in self.todo or url in self.busy or url in self.done:
            return False
        self.vprint('adding', url, max_redirect)
        self.todo[url] = max_redirect
        return True

    @asyncio.coroutine
    def crawl(self):
        """Run the crawler until all finished."""
        with (yield from self.termination):
            while self.todo or self.busy:
                if self.todo:
                    url, max_redirect = self.todo.popitem()
                    fetcher = Fetcher(url,
                                      crawler=self,
                                      max_redirect=max_redirect,
                                      max_tries=self.max_tries,
                                      verbose=self.verbose)
                    self.busy[url] = fetcher
                    fetcher.task = asyncio.Task(self.fetch(fetcher))
                else:
                    yield from self.termination.wait()
        self.t1 = time.time()

    @asyncio.coroutine
    def fetch(self, fetcher):
        """Call the Fetcher's fetch(), with a limit on concurrency.

        Once this returns, move the fetcher from busy to done.
        """
        url = fetcher.url
        with (yield from self.governor):
            try:
                yield from fetcher.fetch()  # Fetcher gonna fetch.
            except Exception:
                # Force GC of the task, so the error is logged.
                fetcher.task = None
                raise
        with (yield from self.termination):
            self.done[url] = fetcher
            del self.busy[url]
            self.termination.notify()

    def report(self, file=None):
        """Print a report on all completed URLs."""
        if self.t1 is None:
            self.t1 = time.time()
        dt = self.t1 - self.t0
        if dt and self.max_tasks:
            speed = len(self.done) / dt / self.max_tasks
        else:
            speed = 0
        if self.verbose > 0:
            print('*** Report ***', file=file)
            try:
                for url, fetcher in sorted(self.done.items()):
                    fetcher.report(file=file)
            except KeyboardInterrupt:
                print('\nInterrupted', file=file)
        print('Crawled', len(self.done),
              'urls in %.3f secs' % dt,
              '(max_tasks=%d)' % self.max_tasks,
              '(%.3f urls/sec/task)' % speed,
              file=file)


def main():
    """Main program.

    Parse arguments, set up event loop, run crawler, print report.
    """
    args = ARGS.parse_args()
    if not args.roots:
        print('Use --help for command line help')
        return

    if args.iocp:
        from asyncio.windows_events import ProactorEventLoop
        loop = ProactorEventLoop()
        asyncio.set_event_loop(loop)
    elif args.select:
        loop = asyncio.SelectorEventLoop()
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()

    roots = {fix_url(root) for root in args.roots}

    crawler = Crawler(roots,
                      exclude=args.exclude,
                      strict=args.strict,
                      max_redirect=args.max_redirect,
                      max_tries=args.max_tries,
                      max_tasks=args.max_tasks,
                      max_pool=args.max_pool,
                      verbose=args.verbose)
    try:
        loop.run_until_complete(crawler.crawl())  # Crawler gonna crawl.
    except KeyboardInterrupt:
        sys.stderr.flush()
        print('\nInterrupted\n')
    finally:
        crawler.report()
        crawler.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    main()
