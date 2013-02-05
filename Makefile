# Some simple testing tasks (sorry, UNIX only).

PYTHON=python3
COVERAGE=coverage3
NONTESTS=`find tulip -name [a-z]\*.py ! -name \*_test.py`
FLAGS=

test:
	$(PYTHON) runtests.py -v $(FLAGS)

testloop:
	while sleep 1; do $(PYTHON) runtests.py -v $(FLAGS); done

# See README for coverage installation instructions.
cov coverage:
	$(COVERAGE) run --branch runtests.py -v $(FLAGS)
	$(COVERAGE) html $(NONTESTS)
	$(COVERAGE) report -m $(NONTESTS)
	echo "open file://`pwd`/htmlcov/index.html"

check:
	$(PYTHON) check.py

clean:
	rm -rf __pycache__ */__pycache__
	rm -f *.py[co] */*.py[co]
	rm -f *~ */*~
	rm -f .*~ */.*~
	rm -f @* */@*
	rm -f '#'*'#' */'#'*'#'
	rm -f *.orig */*.orig
	rm -f .coverage
	rm -rf htmlcov
