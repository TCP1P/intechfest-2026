# HTB Challenge Template Makefile

.PHONY: help generate clean list validate update-readme install-deps

# Default target
help:
	@echo "HTB Challenge Template - Available commands:"
	@echo ""
	@echo "  make generate                   - Generate all challenge templates"
	@echo "  make list                       - List all available challenges"
	@echo "  make validate                   - Validate challenge configuration"
	@echo "  make update-readme              - Update README.md with current challenges"
	@echo "  make clean                      - Clean generated challenges; preserve brainstorms"
	@echo "  make install-deps               - Install required dependencies"
	@echo "  make help                       - Show this help message"
	@echo ""
	@echo "Examples:"
	@echo "  make generate"
	@echo "  make list"
	@echo "  make validate"

# Generate all challenge templates
generate:
	@echo "Generating all challenge templates..."
	@htb generate

# List all available challenges
list:
	@echo "Available challenges:"
	@htb list

# Clean generated challenges
clean:
	@echo "Cleaning generated challenges..."
	@htb clean

# Install required dependencies
install-deps:
	@echo "Installing required dependencies..."
	@echo "No additional dependencies required for Go binary"

# Validate challenge configuration
validate:
	@echo "Validating challenge configuration..."
	@htb validate

# Update README with current challenges
update-readme:
	@echo "Updating README.md with current challenges..."
	@htb update-readme
