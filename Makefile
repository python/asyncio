# Some simple testing tasks (sorry, UNIX only).

PYTHON=python3
VERBOSE=$(V)
V=	0
FLAGS=

test:
	$(PYTHON) runtests.py -v $(VERBOSE) $(FLAGS)

vtest:
	$(PYTHON) runtests.py -v 1 $(FLAGS)

testloop:
	while sleep 1; do $(PYTHON) runtests.py -v $(VERBOSE) $(FLAGS); done

# See runtests.py for coverage installation instructions.
cov coverage:
	$(PYTHON) runtests.py --coverage -v $(VERBOSE) $(FLAGS)

check:
	$(PYTHON) check.py

# Requires "pip install pep8".
pep8: check
	pep8 --ignore E125,E127,E226 tests asyncio

clean:
	rm -rf `find . -name __pycache__`
	rm -f `find . -type f -name '*.py[co]' `
	rm -f `find . -type f -name '*~' `
	rm -f `find . -type f -name '.*~' `
	rm -f `find . -type f -name '@*' `
	rm -f `find . -type f -name '#*#' `
	rm -f `find . -type f -name '*.orig' `
	rm -f `find . -type f -name '*.rej' `
	rm -rf dist
	rm -f .coverage
	rm -rf htmlcov
	rm -rf build
	rm -f MANIFEST


# Push a source distribution for Python 3.3 to PyPI.
# You must update the version in setup.py first.
# The corresponding action on Windows is pypi.bat.
# A PyPI user configuration in ~/.pypirc is required.
pypi: clean
	python3.3 setup.py sdist upload
