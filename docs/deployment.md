# Deployment

Local development uses the root Compose project. Production uses separate services behind an HTTPS gateway. This repository supplies production Dockerfiles and a manual GitHub Actions workflow. Production provisioning, the SSH hook and the site's backup policy belong to the infrastructure operator.

## Release workflow

On the GitHub repository for the monorepo, protect `main` and create a **production** GitHub Environment. Restrict its deployment branches to `main`, configure required reviewers, prevent self-review and restrict bypasses. Limit workflow changes and environment administration to trusted maintainers. The workflow must be present on the default branch before GitHub offers its manual dispatch.

Use **Actions → Deploy approved production release → Run workflow** and choose `main` when an actual release is authorized. The workflow tests the commit selected by that dispatch, including all ten migrated checks and the full-stack integration smoke. Following approval, the release job builds both production Dockerfiles for Linux AMD64 and publishes to GHCR using its job-scoped `GITHUB_TOKEN` with `packages: write`. Configure the repository's write access to both packages. The workflow records both digests in the run summary and sends those exact references to the host. It serializes production releases.

### Production environment configuration

| Kind | Name | Value supplied by the operator |
| --- | --- | --- |
| Variable | `PUBLIC_API_ORIGIN` | Public HTTPS origin of the API, with optional port, no path or trailing slash. Next.js embeds it in browser assets at build time. |
| Variable | `PUBLIC_FRONTEND_ORIGIN` | Public HTTPS origin of the storefront, with optional port, no path or trailing slash. The release job checks the deployed sign-in page. |
| Variable | `DEPLOY_HOST` | DNS name or IPv4 address of the SSH endpoint reachable from the release runner, on port 22. |
| Variable | `DEPLOY_USER` | Dedicated Unix account restricted to the release command. |
| Secret | `DEPLOY_SSH_KEY` | Multiline OpenSSH private key dedicated to that restricted account. Install its public key on the host. |
| Secret | `DEPLOY_KNOWN_HOSTS` | Exact OpenSSH known-hosts entry for `DEPLOY_HOST`, obtained through a verified infrastructure channel. Validate the fingerprint out of band and update it deliberately on rotation. |

Select a runner and firewall policy that provide controlled SSH access to the host. The supplied job runs on a GitHub-hosted Ubuntu runner. Private networks need organization-managed runner connectivity before enabling the release. Store the host's pull-only credentials for private GHCR packages in its credential store. Supply application credentials directly to the host through its secret manager.

### Host command

Provision a root-owned executable, for example `/usr/local/libexec/mallbyte-release`, and attach the deployment public key to it with OpenSSH's `restrict,command="/usr/local/libexec/mallbyte-release"` authorized-key options. Deny interactive access, port forwarding, agent forwarding and arbitrary Docker access for this account. The deploy workflow sends four space-delimited fields in `SSH_ORIGINAL_COMMAND`:

```text
deploy <40-hex-commit> ghcr.io/<lowercase-owner>/<lowercase-repo>-backend@sha256:<64-hex-digest> ghcr.io/<lowercase-owner>/<lowercase-repo>-frontend@sha256:<64-hex-digest>
```

The hook must parse the fields as data, validate the verb, field count, commit, two exact package names and digest syntax, and reject other input. Never evaluate `SSH_ORIGINAL_COMMAND` as shell code. Bind mount paths, environment files and permission boundaries to server-owned configuration. Record the SHA and both digests with the release and return a failing status if rollout or health verification fails.

1. Acquire a server-side release lock, record the running digests, and verify the backup and rollback prerequisites.
2. Pull the exact digests with host-side read-only credentials. Configure `web`, `celery_worker` and the singleton `celery_beat` with the backend digest and the Next.js process with the frontend digest. The backend production Compose file contains build definitions. Supply image references in the host-managed deployment manifest and run with rebuilding disabled.
3. Run Django deployment checks, apply backward-compatible migrations once, and collect static files with the shared static volume mounted. Roll out the API, worker, scheduler, gateway and frontend.
4. Verify frontend HTTPS, API `/healthz/`, gateway `/gateway-healthz`, PostgreSQL/Redis access, a real queue task and a protected API flow. Record the observed outcome. Reapply the previous digests if a compatible release fails. Rehearse a separate data recovery plan for incompatible schema changes.

After the hook completes, the runner also requires HTTP 200 over verified HTTPS from the storefront's `/auth` route and the API's `/healthz/` route, including the API's expected JSON status. Route production frontend traffic through the TLS proxy to the Next.js process on port 3000 and API traffic through the internal gateway on port 8080. The gateway reaches Django on container port 8000. A failed external probe marks the run failed and requires operator review of the release and rollback.

## Runtime prerequisites

- Provision external PostgreSQL and Redis, a TLS reverse proxy, DNS, firewall rules, monitored storage and encrypted off-site backups. Match PostgreSQL client and server major versions for backups and restore drills. Keep the API gateway, web and database listeners private.
- Supply independent `SECRET_KEY` and stable `MFA_FERNET_KEY`, database and Redis credentials, SMTP credentials and any enabled provider credentials. Keep backups of the MFA key under controlled access. Review the complete settings template before provisioning. [Backend production environment](../backend/.env.production.example)
- Set exact `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` and `CORS_ALLOWED_ORIGINS` for the deployed origins. Configure the proxy to replace forwarded headers, Django production settings, secure cookies and certificate-validated database connections. Frontend server-side rendering must be able to reach the API. Configure `INTERNAL_API_BASE_URL` if it uses a different route and ensure that route works with Django's allowed hosts and HTTPS handling.
- Persist public media, private attachments and collected static files on their respective volumes. The gateway serves only public media and static assets. Keep exactly one scheduler and monitor worker delivery.
- Rehearse the release in an isolated staging environment with its own services, credentials and backups. Exercise real OTP/MFA, authenticated and unauthorized user flows, private media, checkout and recovery before the first production release. [Backend staging and recovery](../backend/docs/STAGING_AND_RECOVERY.md) · [Backend production runbook](../backend/docs/PRODUCTION_RUNBOOK.md) · [Frontend production image](../frontend/docs/FRONTEND_PRODUCTION.md)
