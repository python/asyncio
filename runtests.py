"""Run all unittests.

Usage:
  python3 runtests.py [-v] [-q] [pattern] ...

Where:
  -v: verbose
  -q: quiet
  pattern: optional regex patterns to match test ids (default all tests)

Note that the test id is the fully qualified name of the test,
including package, module, class and method,
e.g. 'tests.events_test.PolicyTests.testPolicy'.

runtests.py with --coverage argument is equivalent of:

  $(COVERAGE) run --branch runtests.py -v
  $(COVERAGE) html $(list of files)
  $(COVERAGE) report -m $(list of files)

"""

# Originally written by Beech Horn (for NDB).

import argparse
import logging
import os
import re
import sys
import subprocess
import unittest
import importlib.machinery

assert sys.version >= '3.3', 'Please use Python 3.3 or higher.'

ARGS = argparse.ArgumentParser(description="Run all unittests.")
ARGS.add_argument(
    '-v', action="store", dest='verbose',
    nargs='?', const=1, type=int, default=0, help='verbose')
ARGS.add_argument(
    '-x', action="store_true", dest='exclude', help='exclude tests')
ARGS.add_argument(
    '-q', action="store_true", dest='quiet', help='quiet')
ARGS.add_argument(
    '--tests', action="store", dest='testsdir', default='tests',
    help='tests directory')
ARGS.add_argument(
    '--coverage', action="store", dest='coverage', nargs='?', const='',
    help='enable coverage report and provide python files directory')
ARGS.add_argument(
    'pattern', action="store", nargs="*",
    help='optional regex patterns to match test ids (default all tests)')

COV_ARGS = argparse.ArgumentParser(description="Run all unittests.")
COV_ARGS.add_argument(
    '--coverage', action="store", dest='coverage', nargs='?', const='',
    help='enable coverage report and provide python files directory')


def load_modules(basedir, suffix='.py'):
    def list_dir(prefix, dir):
        files = []

        modpath = os.path.join(dir, '__init__.py')
        if os.path.isfile(modpath):
            mod = os.path.split(dir)[-1]
            files.append(('{}{}'.format(prefix, mod), modpath))

            prefix = '{}{}.'.format(prefix, mod)

        for name in os.listdir(dir):
            path = os.path.join(dir, name)

            if os.path.isdir(path):
                files.extend(list_dir('{}{}.'.format(prefix, name), path))
            else:
                if (name != '__init__.py' and
                    name.endswith(suffix) and
                    not name.startswith(('.', '_'))):
                    files.append(('{}{}'.format(prefix, name[:-3]), path))

        return files

    mods = []
    for modname, sourcefile in list_dir('', basedir):
        if modname == 'runtests':
            continue
        try:
            loader = importlib.machinery.SourceFileLoader(modname, sourcefile)
            mods.append((loader.load_module(), sourcefile))
        except SyntaxError:
            raise
        except Exception as err:
            print("Skipping '{}': {}".format(modname, err), file=sys.stderr)

    return mods


def load_tests(testsdir, includes=(), excludes=()):
    mods = [mod for mod, _ in load_modules(testsdir)]

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    for mod in mods:
        for name in set(dir(mod)):
            if name.endswith('Tests'):
                test_module = getattr(mod, name)
                tests = loader.loadTestsFromTestCase(test_module)
                if includes:
                    tests = [test
                             for test in tests
                             if any(re.search(pat, test.id())
                                    for pat in includes)]
                if excludes:
                    tests = [test
                             for test in tests
                             if not any(re.search(pat, test.id())
                                        for pat in excludes)]
                suite.addTests(tests)

    return suite


def runtests():
    args = ARGS.parse_args()

    testsdir = os.path.abspath(args.testsdir)
    if not os.path.isdir(testsdir):
        print("Tests directory is not found: {}\n".format(testsdir))
        ARGS.print_help()
        return

    excludes = includes = []
    if args.exclude:
        excludes = args.pattern
    else:
        includes = args.pattern

    v = 0 if args.quiet else args.verbose + 1

    tests = load_tests(args.testsdir, includes, excludes)
    logger = logging.getLogger()
    if v == 0:
        logger.setLevel(logging.CRITICAL)
    elif v == 1:
        logger.setLevel(logging.ERROR)
    elif v == 2:
        logger.setLevel(logging.WARNING)
    elif v == 3:
        logger.setLevel(logging.INFO)
    elif v >= 4:
        logger.setLevel(logging.DEBUG)
    result = unittest.TextTestRunner(verbosity=v).run(tests)
    sys.exit(not result.wasSuccessful())


def runcoverage(sdir, args):
    """
    To install coverage3 for Python 3, you need:
      - Distribute (http://packages.python.org/distribute/)

      What worked for me:
      - download http://python-distribute.org/distribute_setup.py
         * curl -O http://python-distribute.org/distribute_setup.py
      - python3 distribute_setup.py
      - python3 -m easy_install coverage
    """
    try:
        import coverage
    except ImportError:
        print("Coverage package is not found.")
        print(runcoverage.__doc__)
        return

    sdir = os.path.abspath(sdir)
    if not os.path.isdir(sdir):
        print("Python files directory is not found: {}\n".format(sdir))
        ARGS.print_help()
        return

    mods = [source for _, source in load_modules(sdir)]
    coverage = [sys.executable, '-m', 'coverage']

    try:
        subprocess.check_call(
            coverage + ['run', '--branch', 'runtests.py'] + args)
    except:
        pass
    else:
        subprocess.check_call(coverage + ['html'] + mods)
        subprocess.check_call(coverage + ['report'] + mods)


if __name__ == '__main__':
    if '--coverage' in sys.argv:
        cov_args, args = COV_ARGS.parse_known_args()
        runcoverage(cov_args.coverage, args)
    else:
        runtests()
