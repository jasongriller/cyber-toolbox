# Contributing to rmf-rev5-migrator

Thanks for helping build a tool the A&A community can actually use. This project handles Controlled Unclassified Information (CUI) in production deployments, so a few rules are stricter than a typical repo.

## Ground rules

1. **Never commit real policy documents, CUI, secrets, or AWS credentials.** Test fixtures must be synthetic. CI runs `gitleaks`; a hit fails the build.
2. **Never log document content, prompts, or model responses.** Use the CUI-safe logging helpers in `backend/src/rmf_migrator/common/logging.py`. Log identifiers and counts, never text.
3. **A human stays in the loop.** Do not add flows that auto-apply LLM output to an exported document without a review checkpoint.
4. **Bring-your-own-model.** Never hardcode a Bedrock model ID. It is always configuration.

## You do not need GovCloud

All development runs in commercial AWS regions or fully locally with Bedrock mocked. See [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md). GovCloud is just a Terraform variable profile.

## Workflow

1. Fork and branch from `main` (`feature/…`, `fix/…`).
2. Write tests first where there is real logic (parsers, mappers, handlers). We use `pytest`.
3. Keep changes scoped to one milestone concern where possible.
4. Run the checks below locally before opening a PR.
5. Open a PR describing *what* and *why*; link the milestone.

## Local checks (must pass in CI)

```bash
# Backend
cd backend
ruff check .
ruff format --check .
pytest

# Terraform
cd terraform
terraform fmt -check -recursive
terraform validate
tflint
checkov -d .        # or tfsec .

# Frontend
cd frontend
npm ci
npm run lint
npm test
```

No AWS credentials are used in CI. CI does not deploy anything.

## Commit style

Conventional-ish: `type(scope): summary` (e.g. `feat(parser): extract heading tree from docx`). Keep commits atomic.

## License

By contributing you agree your contributions are licensed under [Apache-2.0](LICENSE).
