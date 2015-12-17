#!/usr/bin/env python3
"""Run asyncio unittests.

Usage:
  python3 runtests.py [flags] [pattern] ...

Patterns are matched against the fully qualified name of the test,
including package, module, class and method,
e.g. 'tests.test_events.PolicyTests.testPolicy'.

For full help, try --help.

runtests.py --coverage is equivalent of:

  $(COVERAGE) run --branch runtests.py -v
  $(COVERAGE) html $(list of files)
  $(COVERAGE) report -m $(list of files)

"""

# Originally written by Beech Horn (for NDB).

import argparse
import gc
import logging
import os
import random
import re
import sys
import unittest
import textwrap
import warnings
import importlib.machinery
try:
    import coverage
except ImportError:
    coverage = None

from unittest.signals import installHandler

assert sys.version >= '3.3', 'Please use Python 3.3 or higher.'

ARGS = argparse.ArgumentParser(description="Run all unittests.")
ARGS.add_argument(
    '-v', action="store", dest='verbose',
    nargs='?', const=1, type=int, default=0, help='verbose')
ARGS.add_argument(
    '-x', action="store_true", dest='exclude', help='exclude tests')
ARGS.add_argument(
    '-f', '--failfast', action="store_true", default=False,
    dest='failfast', help='Stop on first fail or error')
ARGS.add_argument(
    '-c', '--catch', action="store_true", default=False,
    dest='catchbreak', help='Catch control-C and display results')
ARGS.add_argument(
    '--forever', action="store_true", dest='forever', default=False,
    help='run tests forever to catch sporadic errors')
ARGS.add_argument(
    '--findleaks', action='store_true', dest='findleaks',
    help='detect tests that leak memory')
ARGS.add_argument('-r', '--randomize', action='store_true',
                  help='randomize test execution order.')
ARGS.add_argument('--seed', type=int,
                  help='random seed to reproduce a previous random run')
ARGS.add_argument(
    '-q', action="store_true", dest='quiet', help='quiet')
ARGS.add_argument(
    '--tests', action="store", dest='testsdir', default='tests',
    help='tests directory')
ARGS.add_argument(
    '--coverage', action="store_true", dest='coverage',
    help='enable html coverage report')
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
        if modname == 'test_pep492' and (sys.version_info < (3, 5)):
            print("Skipping '{0}': need at least Python 3.5".format(modname),
                  file=sys.stderr)
            continue
        try:
            loader = importlib.machinery.SourceFileLoader(modname, sourcefile)
            mods.append((loader.load_module(), sourcefile))
        except SyntaxError:
            raise
        except unittest.SkipTest as err:
            print("Skipping '{}': {}".format(modname, err), file=sys.stderr)

    return mods


def randomize_tests(tests, seed):
    if seed is None:
        seed = random.randrange(10000000)
    random.seed(seed)
    print("Randomize test execution order (seed: %s)" % seed)
    random.shuffle(tests._tests)


class TestsFinder:

    def __init__(self, testsdir, includes=(), excludes=()):
        self._testsdir = testsdir
        self._includes = includes
        self._excludes = excludes
        self.find_available_tests()

    def find_available_tests(self):
        """
        Find available test classes without instantiating them.
        """
        self._test_factories = []
        mods = [mod for mod, _ in load_modules(self._testsdir)]
        for mod in mods:
            for name in set(dir(mod)):
                if name.endswith('Tests'):
                    self._test_factories.append(getattr(mod, name))

    def load_tests(self):
        """
        Load test cases from the available test classes and apply
        optional include / exclude filters.
        """
        loader = unittest.TestLoader()
        suite = unittest.TestSuite()
        for test_factory in self._test_factories:
            tests = loader.loadTestsFromTestCase(test_factory)
            if self._includes:
                tests = [test
                         for test in tests
                         if any(re.search(pat, test.id())
                                for pat in self._includes)]
            if self._excludes:
                tests = [test
                         for test in tests
                         if not any(re.search(pat, test.id())
                                    for pat in self._excludes)]
            suite.addTests(tests)
        return suite


class TestResult(unittest.TextTestResult):

    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.leaks = []

    def startTest(self, test):
        super().startTest(test)
        gc.collect()

    def addSuccess(self, test):
        super().addSuccess(test)
        gc.collect()
        if gc.garbage:
            if self.showAll:
                self.stream.writeln(
                    "    Warning: test created {} uncollectable "
                    "object(s).".format(len(gc.garbage)))
            # move the uncollectable objects somewhere so we don't see
            # them again
            self.leaks.append((self.getDescription(test), gc.garbage[:]))
            del gc.garbage[:]


class TestRunner(unittest.TextTestRunner):
    resultclass = TestResult

    def run(self, test):
        result = super().run(test)
        if result.leaks:
            self.stream.writeln("{} tests leaks:".format(len(result.leaks)))
            for name, leaks in result.leaks:
                self.stream.writeln(' '*4 + name + ':')
                for leak in leaks:
                    self.stream.writeln(' '*8 + repr(leak))
        return result


def _runtests(args, tests):
    v = 0 if args.quiet else args.verbose + 1
    runner_factory = TestRunner if args.findleaks else unittest.TextTestRunner
    if args.randomize:
        randomize_tests(tests, args.seed)
    runner = runner_factory(verbosity=v, failfast=args.failfast)
    sys.stdout.flush()
    sys.stderr.flush()
    return runner.run(tests)


def runtests():
    # Print all warnings to the stdout.
    warnings.simplefilter("always")

    args = ARGS.parse_args()

    if args.coverage and coverage is None:
        URL = "bitbucket.org/pypa/setuptools/raw/bootstrap/ez_setup.py"
        print(textwrap.dedent("""
            coverage package is not installed.

            To install coverage3 for Python 3, you need:
              - Setuptools (https://pypi.python.org/pypi/setuptools)

              What worked for me:
              - download {0}
                 * curl -O https://{0}
              - python3 ez_setup.py
              - python3 -m easy_install coverage
        """.format(URL)).strip())
        sys.exit(1)

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
    failfast = args.failfast

    if args.coverage:
        cov = coverage.coverage(branch=True,
                                source=['asyncio'],
                                )
        cov.start()

    logger = logging.getLogger()
    if v == 0:
        level = logging.CRITICAL
    elif v == 1:
        level = logging.ERROR
    elif v == 2:
        level = logging.WARNING
    elif v == 3:
        level = logging.INFO
    elif v >= 4:
        level = logging.DEBUG
    logging.basicConfig(level=level)

    finder = TestsFinder(args.testsdir, includes, excludes)
    if args.catchbreak:
        installHandler()
    import asyncio.coroutines
    if asyncio.coroutines._DEBUG:
        print("Run tests in debug mode")
    else:
        print("Run tests in release mode")
    try:
        tests = finder.load_tests()
        if args.forever:
            while True:
                result = _runtests(args, tests)
                if not result.wasSuccessful():
                    sys.exit(1)
        else:
            result = _runtests(args, tests)
            sys.exit(not result.wasSuccessful())
    finally:
        if args.coverage:
            cov.stop()
            cov.save()
            cov.html_report(directory='htmlcov')
            print("\nCoverage report:")
            cov.report(show_missing=False)
            here = os.path.dirname(os.path.abspath(__file__))
            print("\nFor html report:")
            print("open file://{}/htmlcov/index.html".format(here))


if __name__ == '__main__':
    runtests()
