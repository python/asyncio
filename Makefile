PYTHON=python3
COVERAGE=coverage3
NONTESTS=`find tulip -name [a-z]\*.py ! -name \*_test.py`

test:
	$(PYTHON) runtests.py -v

testloop:
	while sleep 1; do $(PYTHON) runtests.py -v; done

cov coverage:
	$(COVERAGE) run runtests.py -v
	$(COVERAGE) html $(NONTESTS)
	$(COVERAGE) report -m $(NONTESTS)
	echo "open file://`pwd`/htmlcov/index.html"

check:
	$(PYTHON) check.py

clean:
	rm -f *.py[co] */*.py[co]
	rm -f *~ */*~
	rm -f .*~ */.*~
	rm -f @* */@*
	rm -f '#'*'#' */'#'*'#'
	rm -f .coverage
	rm -rf htmlcov
