# platform

Shared toolbox infrastructure lands here as its own Terraform root with its own
state: the shared user pool and per-app clients, cross-app SSM parameters, and
later the custom domain with its portal shell (one hostname, path-mapped to each
app, one login). Empty until the first shared component ships.
