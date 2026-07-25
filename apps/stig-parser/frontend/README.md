# STIG Condenser — React SPA (GovCloud)

The frontend for the GovCloud deployment. The Flask UI in `../app/` is **not**
replaced by this — it remains the zero-infrastructure way to run the tool
locally or air-gapped. This SPA exists because the GovCloud runtime is
serverless and async, and that API needs a client.

**Design:** `../docs/superpowers/specs/2026-07-14-react-frontend-design.md`

## Develop

Two dev modes:

- **UI-only** — `npm install && npm run dev` here in `frontend/`. Plain http,
  no backend; fine for component/style work, but API calls fail.
- **`devfull`** — `npm run dev` from the **repo root**. Serves the SPA over
  https (`@vitejs/plugin-basic-ssl`, self-signed) on a pinned `:5173` and
  proxies `/config`, `/uploads`, and `/jobs` to the live army-dev API, so real
  Cognito login and S3 uploads work end-to-end (uploads-bucket CORS only
  admits https origins). Copy `.env.devfull.local.example` to
  `.env.devfull.local` (gitignored; Vite loads it automatically for this
  mode) and fill it in from `terraform output` — `VITE_DEV_PROXY_TARGET` is
  `api_invoke_url`, the `VITE_COGNITO_*` values are the pool/client outputs.
  The browser will warn about the self-signed certificate on first load —
  accept it once per browser profile.

## Test

```sh
npm test          # Vitest + React Testing Library + axe
npm run e2e       # Playwright against a mocked API
npx tsc --noEmit  # types
```

There are no tests against a live API: that would need GovCloud credentials, and
public CI does not have them (and must not).

## Build & deploy

```sh
VITE_API_BASE=https://<private-api>/v1 npm run build   # -> dist/
aws s3 sync dist/ s3://<spa_bucket>/ --delete          # operator/CD step
```

The bucket is `spa_bucket` from the Terraform `api` module, served through the
Private API Gateway S3 proxy (D6). Like `terraform apply`, the sync is an
operator action — public CI builds and tests the bundle but never holds
credentials.

## Notes

- **Uploads bypass the API.** Files are PUT straight to S3 with presigned urls,
  which is what keeps a 200 MB scan clear of API Gateway's 29-second ceiling.
  Presigned urls live 15 minutes; a slow upload can outlive one, and the UI
  reports that rather than pretending the job started.
- **The AI toggle is disabled with a stated reason** when no Bedrock model is
  approved. Off is never silent.
- **The stylesheet is ported from the Flask app** and keeps its class names, so
  the audited WCAG AA contrast pairs and focus rings carry over unchanged. Do
  not restyle it casually.
