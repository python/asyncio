"""Run all unittests.

Usage:
  python3 runtests.py [-v] [-q] [pattern] ...

Where:
  -v: verbose
  -q: quiet
  pattern: optional regex patterns to match test ids (default all tests)

Note that the test id is the fully qualified name of the test,
including package, module, class and method,
e.g. 'tulip.events_test.PolicyTests.testPolicy'.
"""

# Originally written by Beech Horn (for NDB).

import re
import sys
import unittest

assert sys.version >= '3.3', 'Please use Python 3.3 or higher.'

def load_tests(includes=(), excludes=()):
  mods = ['events', 'futures', 'tasks']
  test_mods = ['%s_test' % name for name in mods]
  tulip = __import__('tulip', fromlist=test_mods)

  loader = unittest.TestLoader()
  suite = unittest.TestSuite()

  for mod in [getattr(tulip, name) for name in test_mods]:
    for name in set(dir(mod)):
      if name.endswith('Tests'):
        test_module = getattr(mod, name)
        tests = loader.loadTestsFromTestCase(test_module)
        if includes:
          tests = [test
                   for test in tests
                   if any(re.search(pat, test.id()) for pat in includes)]
        if excludes:
          tests = [test
                   for test in tests
                   if not any(re.search(pat, test.id()) for pat in excludes)]
        suite.addTests(tests)

  return suite


def main():
  excludes = []
  includes = []
  patterns = includes  # A reference.
  v = 1
  for arg in sys.argv[1:]:
    if arg.startswith('-v'):
      v += arg.count('v')
    elif arg == '-q':
      v = 0
    elif arg == '-x':
        if patterns is includes:
            patterns = excludes
        else:
            patterns = includes
    elif arg and not arg.startswith('-'):
      patterns.append(arg)
  tests = load_tests(includes, excludes)
  result = unittest.TextTestRunner(verbosity=v).run(tests)
  sys.exit(not result.wasSuccessful())


if __name__ == '__main__':
  main()
