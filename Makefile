PYTHON=python3.3

test:
	$(PYTHON) main.py -v

profile:
	$(PYTHON) -m profile -s time main.py

time:
	$(PYTHON) p3time.py

ytime:
	$(PYTHON) yyftime.py

check:
	$(PYTHON) longlines.py
