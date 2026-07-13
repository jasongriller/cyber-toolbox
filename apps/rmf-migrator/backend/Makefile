# Backend build + dev tasks.
#
# `make build` produces build/rmf-migrator-lambda.zip containing rmf_migrator/
# plus its third-party dependencies at the archive root, which is the layout the
# Terraform module's Lambda handlers expect (handler = rmf_migrator.handlers.*).

PYTHON ?= python3
BUILD_DIR := build
PKG_DIR := $(BUILD_DIR)/package
ZIP := $(BUILD_DIR)/rmf-migrator-lambda.zip

.PHONY: build clean test lint fmt

build: clean
	mkdir -p $(PKG_DIR)
	$(PYTHON) -m pip install . --target $(PKG_DIR)
	# Drop test/build metadata that bloats the artifact.
	rm -rf $(PKG_DIR)/*.dist-info $(PKG_DIR)/*.egg-info
	cd $(PKG_DIR) && zip -r -q ../../$(ZIP) . -x '*.pyc' -x '*__pycache__*'
	@echo "built $(ZIP)"

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
