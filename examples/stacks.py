"""Crude demo for print_stack()."""


from asyncio import *


@coroutine
def helper(r):
    print('--- helper ---')
    for t in Task.all_tasks():
        t.print_stack()
    print('--- end helper ---')
    line = yield from r.readline()
    1/0
    return line

def doit():
    l = get_event_loop()
    lr = l.run_until_complete
    r, w = lr(open_connection('python.org', 80))
    t1 = async(helper(r))
    for t in Task.all_tasks(): t.print_stack()
    print('---')
    l._run_once()
    for t in Task.all_tasks(): t.print_stack()
    print('---')
    w.write(b'GET /\r\n')
    w.write_eof()
    try:
        lr(t1)
    except Exception as e:
        print('catching', e)
    finally:
        for t in Task.all_tasks():
            t.print_stack()
    l.close()


def main():
    doit()


if __name__ == '__main__':
    main()
