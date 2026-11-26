# Monorepo migration and readiness

## Scope and provenance

The integration branch starts at `04329d2f`. Its two-parent backend import follows the two-parent frontend import at `fbf72e7c`. Both original tips remain ancestors. The audit inventoried 551 backend and 438 frontend tracked files at the import, including application code, migrations, manifests, lockfiles, build contexts, tests, operator tooling, documentation and all ten nested workflows.

| Audit finding | Integration change |
| --- | --- |
| GitHub could not discover either component's workflows. Commands assumed the repository root belonged to that component. | Moved all ten workflows to the root, scoped triggers, corrected working directories, lockfile caches and artifact paths, pinned actions and retained the original jobs. |
| Backend and frontend dev environments started independently. A server-rendered CMS fetch used browser localhost inside Next.js. | Added the root stack, separate public and server API origins, Redis-backed services, health checks, a migration barrier and a database-backed SSR regression check. |
| Standalone development published listeners broadly and accepted arbitrary credentialed CORS origins. Console OTP still required SMTP credentials. | Bound local listeners to loopback, restricted origins, generated per-checkout credentials and exercised console delivery and production credential requirements. |
| Django entrypoints and settings discovery depended on invocation context. Health probes attempted observability writes. | Selected explicit runtime settings, scoped dotenv to the backend, made liveness independent of request-log writes and enforced Redis and disabled mock payments in production settings. |
| Windows checkout converted executable shell scripts to CRLF. Private uploads and a report test depended on filesystem or clock behavior. | Enforced LF, checked private media containment across drives, and used an explicit in-window timestamp while retaining all report assertions. |
| The original host runner selected only component manifests. | Added an explicit root development target. Independent approval, the fixed restart allowlist, and host-only Docker access remain in force. |

## Validation record

| Check | Observed result |
| --- | --- |
| Django tests | All **1,032 passed** in 756.380 seconds with the sequential Django runner, Python 3.12 and an isolated PostgreSQL 17.10 cluster on Windows. The host used local-memory cache and Celery transport. The Docker CI suite uses Python 3.13, PostgreSQL 16 and Redis. |
| Django static and configuration checks | Ruff passed across `apps` and `config`. Python sources compiled. System checks, migration drift, strict OpenAPI validation and production `check --deploy --fail-level WARNING` passed. Fresh production processes rejected a local-memory cache and disabled demo/mock settlement flags with development environment flags set. |
| Host operations | Eight existing tests passed using a POSIX Python runtime and Bash on Windows. The restore safety check refused a production-named target. |
| Frontend | Final locked Next.js 16.4.5 passed 678 Vitest tests in 105 files, 41 pinned Chromium Playwright tests, TypeScript and the production build (42 static generation entries). ESLint reported zero errors and one existing effect-dependency warning in `product-list.tsx`. Standalone server and a production JavaScript asset returned HTTP 200. Host Node was 26.13.1. CI targets Node 22. |
| Live integration | With native Django, Next.js, Chromium and PostgreSQL, a seeded published page rendered via SSR and the browser fetched the catalog with credentialed CORS. Console OTP, scoped HttpOnly refresh, authenticated profile, customer denial on an admin endpoint, refresh, logout and session revocation were exercised. Unauthorized browser access was denied. |
| Dependency checks | `npm ci` and `npm audit` passed after the Next.js security patch. The earlier lockfile reported a moderate direct Next.js finding. Python `pip check` passed and strict `pip-audit` reported no known vulnerabilities in the 60 resolved production packages examined. |
| Compose and workflows | Compose v2.39.4 rendered seven configurations covering root, backend base/dev/profiles/staging/production and frontend staging. Checked dependency conditions, ports, networks, volumes, contexts and overrides. The development build plan resolved two build targets. All 12 workflow definitions passed actionlint. Workflow shell syntax, k6 script syntax and shell LF attributes were checked. |
| Docker / hosted CI / deployment | Both local production image builds stopped before building because this Windows machine has no Docker Engine. The standalone Compose CLI validated configuration only. Linux runtime startup, image builds, Redis-backed task delivery, Nginx and hosted Actions require their own runs. No release was dispatched. |

The first recovered run had eight errors. Cross-drive path handling and a financial report fixture accounted for them. A subsequent complete run passed 1,031 of 1,032 and exposed one additional refund fixture at the exclusive time cutoff. Its complete module passed 14/14 after correction, followed by the full green run above. An experimental four-process Windows run encountered RBAC fixture conflicts and could not serialize a worker traceback. CI and the documented verification use the sequential runner.

## Secret review

Gitleaks 8.28.0 flagged four locations in the current source tree. Two are synthetic observability test keys, one is a refund test's idempotency key, and one is a README bearer placeholder. The scanner correctly exits with findings. No current deployable credential was identified. A separate redacted scan of changes since the subtree import found no leaks. The ignored local environment and QA directories are outside the tracked snapshot.

A redacted scan of the integration ancestry also found an **expired JWT** in the historical backend file `test_profile.py` at `087495f6`. The script used a loopback endpoint. The token expiry was **March 11, 2026**, and the file was removed at `10a19203`. The token's issuer and original environment were not authenticated during this review. Historical findings also included examples/tests in a formerly tracked virtual environment. Both original histories already existed on monorepo main before this branch. No token was submitted to a service, included in this report, or added to the integration diff.

## Release gates

Run the isolated Linux verification procedure on the same commit and require green hosted CI on the reviewed PR. Configure branch protection with the workflows' path filters in mind. Validate real cached authentication, queue processing, HTTPS, SMTP, external providers, private media, backup restoration and rollback with isolated staging resources before promotion. The supplied CD job builds Linux AMD64 images. The protected environment, reviewers and server-side restricted deployment command must be provisioned by the operator. [Linux verification](linux-verification.md) · [Deployment contract](deployment.md)

The complete commit list for this branch, including this report, is available with:

```sh
git log --reverse --first-parent --oneline 04329d2f..chore/monorepo-integration
```
