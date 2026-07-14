# Backend build + dev tasks.
#
# `make build` produces build/rmf-migrator-lambda.zip containing rmf_migrator/
# plus its third-party dependencies at the archive root, which is the layout the
# Terraform module's Lambda handlers expect (handler = rmf_migrator.handlers.*).
#
# The build itself lives in ../scripts/build_lambda.py, not in this Makefile, so
# it runs on any host OS with no `make` or `zip` installed:
#
#     python scripts/build_lambda.py
#
# That script also cross-targets the Lambda runtime (manylinux / cp312). This is
# not cosmetic: lxml and pydantic-core are compiled extensions, and building them
# on Windows or macOS yields host-native binaries that import locally and then
# fail at runtime in Lambda.

PYTHON ?= python3
BUILD_DIR := build

.PHONY: build clean test lint fmt

build:
	$(PYTHON) ../scripts/build_lambda.py

clean:
	rm -rf $(BUILD_DIR)

test:
	PYTHONPATH=src $(PYTHON) -m pytest

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

fmt:
	$(PYTHON) -m ruff format .
	$(PYTHON) -m ruff check --fix .
