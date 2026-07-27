# platform

Shared toolbox infrastructure as its own Terraform root with its own state key
(`platform/<env>` in the same state bucket the apps use). Owns the shared user
pool (`cyber-toolbox-<env>-users`, admin-created accounts, password-only sign-in —
set `mfa_configuration = "ON"` to require an authenticator app, which the SPA
already has screens for) with one SPA client per app, published to SSM under
`<ssm_prefix>/cognito_user_pool_id`, `.../cognito_user_pool_arn`, and
`.../cognito_client_id/<client-name>`. Apps consume the SSM parameters — never
this root's state. Deploy via `./deploy.sh` (guards the account like the app
script). Later: the custom domain + portal shell (one hostname, path-mapped to
each app, one login).

Adding an app = one entry in `app_client_names` + that app reading its client
id from SSM.
