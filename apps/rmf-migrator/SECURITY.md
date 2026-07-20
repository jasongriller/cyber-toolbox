# Security Policy

## Reporting a vulnerability

**Do not open a public GitHub issue for security vulnerabilities.**

Report privately via GitHub's [private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability) ("Report a vulnerability" under the Security tab), or email the maintainers listed in the repository profile. We aim to acknowledge within 5 business days.

Please include: affected component, version/commit, reproduction steps, and impact.

## Scope and threat model

This tool is designed to be self-hosted inside the adopter's own AWS account and to process Controlled Unclassified Information (CUI). Security-relevant properties the project commits to:

- **No anonymous API access by default.** Production mode requires AWS SigV4 on every API route and places Lambda compute in the adopter's VPC. API Gateway HTTP API v2 still has an AWS edge hostname, so adopters requiring network-private browser access must put an internal signing proxy/portal in front of it.
- **CUI never enters logs.** Document text, prompts, and model responses are excluded from CloudWatch by design. Reports of content leaking into logs are treated as high severity.
- **Encryption at rest** with a customer-managed KMS key across S3, DynamoDB, and SQS.
- **Least-privilege IAM.** Lambda roles are scoped to named resources.
- **Prompt-injection resistance.** Uploaded document text is untrusted input to the LLM. We use Bedrock Guardrails where available plus prompt hardening and output validation. Bypasses are in scope.

## What is *not* in scope

- The security of the adopter's own AWS account, network, IdP, or VPN.
- Vulnerabilities in third-party dependencies without a demonstrated exploit path through this tool (report those upstream; we still want to know).

## Supported versions

Until the first tagged release, only `main` is supported.
