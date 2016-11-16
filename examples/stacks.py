"""Crude demo for print_stack()."""

import asyncio


@asyncio.coroutine
def helper(r):
    print('--- helper ---')
    for t in asyncio.Task.all_tasks():
        t.print_stack()
    print('--- end helper ---')
    line = yield from r.readline()
    1 / 0
    return line


def doit():
    l = asyncio.get_event_loop()
    lr = l.run_until_complete
    r, w = lr(asyncio.open_connection('python.org', 80))
    t1 = asyncio.async(helper(r))
    for t in asyncio.Task.all_tasks():
        t.print_stack()
    print('---')
    l._run_once()
    for t in asyncio.Task.all_tasks():
        t.print_stack()
    print('---')
    w.write(b'GET /\r\n')
    w.write_eof()
    try:
        lr(t1)
    except Exception as e:
        print('catching', e)
    finally:
        for t in asyncio.Task.all_tasks():
            t.print_stack()
    l.close()


def main():
    doit()


if __name__ == '__main__':
    main()
