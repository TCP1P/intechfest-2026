RSCTF ?= rsctf

.PHONY: help list validate matrix checkers solutions components shellcheck check

help:
	@echo "INTECHFEST 2026 challenge repository commands"
	@echo ""
	@echo "  make list       List event and challenge manifests"
	@echo "  make validate   Validate both events with the official rsctf CLI"
	@echo "  make matrix     Print the dynamically discovered Docker build matrix"
	@echo "  make checkers   Run every checker regression suite"
	@echo "  make solutions  Compile every tracked Python solution file"
	@echo "  make components Run Python and Go component tests/static analysis"
	@echo "  make shellcheck Parse every tracked POSIX shell entry point"
	@echo "  make check      Run all local repository checks"

list:
	@find events -type f \( -name .gzevent -o -name challenge.yaml -o -name challenge.yml \) -print | sort

validate:
	$(RSCTF) challenge check --deny-warnings .

matrix:
	$(RSCTF) challenge matrix .

checkers:
	@find events -path '*/checker/test_*.py' -type f -print0 | sort -z | \
		xargs -0 -r -n1 sh -c 'cd "$$(dirname "$$1")" && python3 "$$(basename "$$1")"' sh

solutions:
	@find events -path '*/solution/*.py' -type f -print0 | sort -z | xargs -0 -r -n1 python3 -m py_compile

components:
	@find events -path '*/src/test_*.py' -type f -print0 | sort -z | \
		xargs -0 -r -n1 sh -c 'cd "$$(dirname "$$1")" && python3 "$$(basename "$$1")"' sh
	@find events -name go.mod -type f -print0 | sort -z | xargs -0 -r sh -c '\
		for module do \
			directory=$$(dirname "$$module"); \
			printf "Testing %s\\n" "$$directory"; \
			(cd "$$directory" && go test ./... && go vet ./...) || exit; \
		done' sh

shellcheck:
	@find events -name '*.sh' -type f -print0 | sort -z | xargs -0 -r sh -c '\
		for file do \
			case $$(head -n 1 "$$file") in \
				*"/bash"*) bash -n "$$file" ;; \
				*) sh -n "$$file" ;; \
			esac || exit; \
		done' sh

check: validate matrix checkers solutions components shellcheck
