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
	$(PYTHON) runtests.py --coverage tulip -v $(VERBOSE) $(FLAGS)

check:
	$(PYTHON) check.py

clean:
	rm -rf `find . -name __pycache__`
	rm -f `find . -type f -name '*.py[co]' `
	rm -f `find . -type f -name '*~' `
	rm -f `find . -type f -name '.*~' `
	rm -f `find . -type f -name '@*' `
	rm -f `find . -type f -name '#*#' `
	rm -f `find . -type f -name '*.orig' `
	rm -f `find . -type f -name '*.rej' `
	rm -f .coverage
	rm -rf htmlcov
