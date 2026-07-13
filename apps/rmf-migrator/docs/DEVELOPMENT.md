# Development

You do **not** need an AWS GovCloud account (or any AWS account) to develop and test the backend. Tests run fully offline against mocked AWS via [`moto`](https://github.com/getmoto/moto).

## Prerequisites

- Python 3.12+
- Node 20+ (for the frontend, from M2 on)
- Terraform 1.6+ (only to work on infra)

## Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

# Run the suite (offline, moto-mocked AWS)
pytest

# Lint + format
ruff check .
ruff format --check .

# Build the Lambda deployment zip
make build   # -> build/rmf-migrator-lambda.zip
```

### How tests avoid AWS and GovCloud

- **AWS**: `tests/conftest.py` spins up moto-backed S3, DynamoDB, and SQS and hands handlers a `Deps` bundle pointed at them. No credentials, no network.
- **Bedrock**: not exercised in M1. From M3, Bedrock calls go through a thin client that is faked in tests; a real model is only touched in a deployed environment.
- **DOCX**: the parser's core logic is tested against synthetic paragraph streams; a few tests build real `.docx` bytes in-memory with `python-docx`.

## Terraform

```bash
cd terraform
terraform fmt -recursive
cd examples/standalone
terraform init -backend=false
terraform validate
```

`terraform validate` needs no cloud credentials. Applying does — see [DEPLOYMENT.md](DEPLOYMENT.md).

## Layout

- `backend/src/rmf_migrator/common` — config, models, logging, storage, repository.
- `backend/src/rmf_migrator/docx` — DOCX parsing.
- `backend/src/rmf_migrator/handlers` — Lambda entrypoints (API + SQS worker).
- `terraform/modules/rmf-migrator` — the reusable module.
- `terraform/examples/standalone` — greenfield root that consumes the module.

## The CUI-safe logging rule

Never log document text, prompts, or model responses. Use `common/logging.py`:

```python
from rmf_migrator.common.logging import log_event, length_of
log_event("document.parsed", document_id=did, section_count=n, char_length=length_of(text))
```

`log_event` raises `ContentInLogError` if you hand it a long string, to catch accidental content leakage in review and tests.
