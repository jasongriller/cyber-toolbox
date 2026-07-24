<!--
Do not include CUI, real policy text, or account identifiers anywhere in this PR.
Test fixtures must be synthetic.
-->

## What & why

<!-- What does this change and what problem does it solve? Link the issue. -->

Closes #

## Type

- [ ] Bug fix
- [ ] Feature
- [ ] Refactor / cleanup
- [ ] Docs
- [ ] Infra (Terraform)
- [ ] Bundled data update (catalogs / baselines / mappings)

## Checklist

- [ ] `ruff check .` and `ruff format --check .` pass (backend)
- [ ] `pytest` passes (backend)
- [ ] `npm run lint` and `npm test` pass (frontend, if touched)
- [ ] `terraform fmt -check -recursive` and `terraform validate` pass (if touched)
- [ ] No document content, prompts, or model responses are logged
- [ ] No secrets, CUI, or real policy text committed
- [ ] The human-in-the-loop checkpoints (mapping approval, draft approval) are preserved
- [ ] Bedrock model id remains configuration (not hardcoded)

## Notes for reviewers

<!-- Anything non-obvious, trade-offs, follow-ups. -->
