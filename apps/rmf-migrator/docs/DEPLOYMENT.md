# Deployment

The tool deploys entirely into your own AWS account via Terraform. It ships two ways to consume the infrastructure:

1. **Standalone root** (`terraform/examples/standalone`) — greenfield adopters. Creates everything.
2. **Reusable module** (`terraform/modules/rmf-migrator`) — drop into your existing toolbox IaC, passing your VPC, subnets, and (optionally) KMS key as inputs.

## 1. Pick a Bedrock model — and prove it works

The model id is pure configuration, so *any* Bedrock model your account has
enabled can drive the tool. Before you deploy, check that your candidate actually
works with it:

```bash
python scripts/check_bedrock_model.py openai.gpt-oss-120b-1:0 --region us-west-2
```

This runs the tool's **real** mapping and drafting prompts against the model and
reports whether it serves the Converse API, accepts a system prompt (where the
prompt-injection hardening lives), and returns JSON the pipeline can parse. It
creates no infrastructure and costs a fraction of a cent.

A pass means the pipeline will work. Run it before `terraform apply`, not after.

## 2. Build the Lambda package

```bash
python scripts/build_lambda.py     # -> backend/build/rmf-migrator-lambda.zip
```

Works on any host OS, with no `make` or `zip` needed. It cross-targets the Lambda
runtime (manylinux / cp312) — this matters, because `lxml` and `pydantic-core` are
compiled extensions and a naive build on Windows or macOS produces host-native
binaries that import locally and then fail at runtime in Lambda. The script
refuses to produce such an archive.

Or download `rmf-migrator-lambda.zip` from a tagged GitHub release.

## 3. Configure

```bash
cd terraform/examples/standalone
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: set bedrock_model_id, lambda_zip_path, region, network_mode
```

## 4. Deploy

```bash
terraform init
terraform apply
```

## GovCloud

Set `region = "us-gov-west-1"` (or `us-gov-east-1`). No other change — the module is partition-aware (`aws-us-gov`) and constructs all ARNs accordingly.

Before deploying, confirm in your account/region:

- The **Bedrock model** you set in `bedrock_model_id` is enabled. Model availability in GovCloud differs from commercial AWS.
- **Bedrock Guardrails** availability. If unavailable, leave `bedrock_guardrail_id` unset; the tool falls back to prompt hardening and output validation.

## Network modes

### `public` (dev / demo)

The HTTP API is reachable directly. Quickest to stand up. Do not put CUI through a public deployment.

### `private` (production, recommended for CUI)

- Set `network_mode = "private"` and pass `vpc_id`, `private_subnet_ids`, and
  the trusted SPA/portal origin(s) in `frame_ancestors`. Those origins become
  the API and presigned-upload CORS allowlist; private mode never defaults CORS
  to `*`.
- Lambdas run inside your VPC with an egress-only security group.
- Every API route requires AWS Signature Version 4 (`AWS_IAM`). Grant trusted
  callers `execute-api:Invoke` on this API. A browser cannot sign these requests
  by itself; normally the SPA calls an internal portal/proxy at `/api`, and that
  trusted proxy signs the upstream request with a tightly scoped IAM role.
- **You must provide VPC endpoints** in that VPC so in-VPC Lambdas reach AWS services without internet egress:

  | Service | Endpoint type |
  |---------|---------------|
  | S3 | Gateway |
  | DynamoDB | Gateway |
  | SQS (`com.amazonaws.<region>.sqs`) | Interface |
  | Bedrock runtime (`com.amazonaws.<region>.bedrock-runtime`) | Interface |
  | CloudWatch Logs (`com.amazonaws.<region>.logs`) | Interface |
  | KMS (`com.amazonaws.<region>.kms`) | Interface |

- API Gateway HTTP API v2 does not offer the REST API `PRIVATE` endpoint type.
  Its AWS edge hostname therefore remains network-reachable, but unsigned or
  unauthorized calls are rejected before Lambda invocation. If policy requires
  the browser-facing endpoint itself to be network-private, expose only your
  internal signing proxy and block direct client use of the output API URL.

## Encryption

All data at rest (S3, DynamoDB, SQS, Lambda env, CloudWatch Logs) is encrypted with a customer-managed KMS key. By default the module creates and rotates one; pass `kms_key_arn` to use your own.

## Tear-down / data deletion

```bash
terraform destroy
```

The DynamoDB table has deletion protection and the documents bucket is not force-destroyed, so `destroy` will refuse until you consciously remove those protections — a guardrail against accidental CUI loss. Per-project hard delete (purging S3 objects + DynamoDB items on demand) is exposed in the app, not via Terraform.
