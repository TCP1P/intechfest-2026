RSCTF ?= rsctf

.PHONY: help list validate matrix checkers solutions check

help:
	@echo "INTECHFEST 2026 challenge repository commands"
	@echo ""
	@echo "  make list       List event and challenge manifests"
	@echo "  make validate   Validate both events with the official rsctf CLI"
	@echo "  make matrix     Print the dynamically discovered Docker build matrix"
	@echo "  make checkers   Run every checker regression suite"
	@echo "  make solutions  Compile every tracked Python solution file"
	@echo "  make check      Run all local repository checks"

list:
	@find events -type f \( -name .gzevent -o -name challenge.yaml -o -name challenge.yml \) -print | sort

validate:
	$(RSCTF) challenge check --deny-warnings .

matrix:
	$(RSCTF) challenge matrix .

checkers:
	@find events -path '*/checker/test_*.py' -type f -execdir python3 '{}' \;

solutions:
	@find events -path '*/solution/*.py' -type f -print0 | sort -z | xargs -0 -r -n1 python3 -m py_compile

check: validate matrix checkers solutions
