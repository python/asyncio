# Some simple testing tasks (sorry, UNIX only).

PYTHON=python3
FLAGS=

test:
	$(PYTHON) runtests.py -v $(FLAGS)

testloop:
	while sleep 1; do $(PYTHON) runtests.py -v $(FLAGS); done

# See README for coverage installation instructions.
cov coverage:
	$(PYTHON) runtests.py --coverage tulip -v $(FLAGS)
	echo "open file://`pwd`/htmlcov/index.html"

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
