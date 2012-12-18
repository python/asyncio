PYTHON=python3
COVERAGE=coverage3
NONTESTS=`find tulip -name [a-z]\*.py ! -name \*_test.py`

test:
	$(PYTHON) runtests.py -v

cov coverage:
	$(COVERAGE) run runtests.py -v
	$(COVERAGE) html $(NONTESTS)
	$(COVERAGE) report -m $(NONTESTS)
	echo "open file://`pwd`/htmlcov/index.html"


main:
	$(PYTHON) main.py -v

echo:
	$(PYTHON) echosvr.py -v

profile:
	$(PYTHON) -m profile -s time main.py

time:
	$(PYTHON) p3time.py

ytime:
	$(PYTHON) yyftime.py

check:
	$(PYTHON) longlines.py
