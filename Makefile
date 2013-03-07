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
	rm -rf __pycache__ */__pycache__
	rm -f *.py[co] */*.py[co]
	rm -f *~ */*~
	rm -f .*~ */.*~
	rm -f @* */@*
	rm -f '#'*'#' */'#'*'#'
	rm -f *.orig */*.orig
	rm -f .coverage
	rm -rf htmlcov
