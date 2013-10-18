"""Search for lines >= 80 chars or with trailing whitespace."""

import os
import sys


def main():
    args = sys.argv[1:] or os.curdir
    for arg in args:
        if os.path.isdir(arg):
            for dn, dirs, files in os.walk(arg):
                for fn in sorted(files):
                    if fn.endswith('.py'):
                        process(os.path.join(dn, fn))
                dirs[:] = [d for d in dirs if d[0] != '.']
                dirs.sort()
        else:
            process(arg)


def isascii(x):
    try:
        x.encode('ascii')
        return True
    except UnicodeError:
        return False


def process(fn):
    try:
        f = open(fn)
    except IOError as err:
        print(err)
        return
    try:
        for i, line in enumerate(f):
            line = line.rstrip('\n')
            sline = line.rstrip()
            if len(line) >= 80 or line != sline or not isascii(line):
                print('{}:{:d}:{}{}'.format(
                    fn, i+1, sline, '_' * (len(line) - len(sline))))
    finally:
        f.close()

main()
