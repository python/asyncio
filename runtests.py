"""Run all unittests."""

# Originally written by Beech Horn (for NDB).

import sys
import unittest


def load_tests():
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
        suite.addTests(tests)

  return suite


def main():
  v = 1
  for arg in sys.argv[1:]:
    if arg.startswith('-v'):
      v += arg.count('v')
    elif arg == '-q':
      v = 0
  result = unittest.TextTestRunner(verbosity=v).run(load_tests())
  sys.exit(not result.wasSuccessful())


if __name__ == '__main__':
  main()
