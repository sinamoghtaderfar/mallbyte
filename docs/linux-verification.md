# Linux checkout and verification

Run these steps on a development host. They use the integration branch, local Docker images, and development data. Production release instructions are separate. [Deployment](deployment.md)

## Clone the integration branch on Linux

In a VS Code Remote SSH terminal on the Linux development host:

```bash
mkdir -p ~/src
git clone --branch chore/monorepo-integration --single-branch https://github.com/sinamoghtaderfar/mallbyte.git ~/src/mallbyte
cd ~/src/mallbyte
git rev-parse HEAD
git status --short --branch
```

For subsequent Windows handoffs, review the clean working tree and send the branch with a normal push to the **monorepo** remote:

```powershell
Set-Location E:\mallbyte
git status --short --branch
git push origin chore/monorepo-integration
```

On an existing clean Linux checkout of the branch, run `git pull --ff-only origin chore/monorepo-integration`. Compare `git rev-parse HEAD` with Windows.

### Fallback when branch transfer through GitHub is unavailable

Use an incremental bundle against the original monorepo import. Replace `mallbyte-linux` with your SSH alias. From the Windows repository root:

```powershell
New-Item -ItemType Directory -Force .local | Out-Null
git bundle create .local/mallbyte-integration.bundle refs/heads/chore/monorepo-integration ^04329d2fb19562999b64c242c6a1d9f5b2251de0
git bundle verify .local/mallbyte-integration.bundle
scp .local/mallbyte-integration.bundle mallbyte-linux:mallbyte-integration.bundle
```

In a Linux clone containing the import commit and without a local integration branch:

```bash
git bundle verify ~/mallbyte-integration.bundle
git fetch ~/mallbyte-integration.bundle refs/heads/chore/monorepo-integration
git switch -c chore/monorepo-integration FETCH_HEAD
git rev-parse HEAD
```

## Start your development stack

```bash
cd ~/src/mallbyte
git branch --show-current
python3 --version
docker version
docker compose version
python3 scripts/init-dev-env.py
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 300
docker compose ps --all
curl -fsS http://localhost:8000/healthz/
curl -fsS -o /dev/null http://localhost:3000/auth
```

Open VS Code's **Ports** panel and forward remote ports 3000 and 8000 to local 3000 and 8000 with private visibility. Alternatively, keep this tunnel open in a separate **local Windows** terminal:

```powershell
ssh -N -L 3000:127.0.0.1:3000 -L 8000:127.0.0.1:8000 mallbyte-linux
```

Visit **http://localhost:3000**. If a host port is occupied, change the corresponding port in the root `.env`, run the Compose start command again and forward the new port to the same local number. Use `docker compose logs --tail 100 migrate web frontend celery_worker celery_beat` to diagnose startup on your own host. Console OTP messages are private development logs.

## Verify a disposable copy of the exact commit

The following **Bash** blocks share one Linux shell. They create a separate checkout snapshot, Compose project, credentials, named volumes and loopback ports. Existing development and production data stay outside the verification project. Choose unused ports if 3300 or 8800 is occupied.

```bash
set -euo pipefail
cd ~/src/mallbyte
test "$(git branch --show-current)" = chore/monorepo-integration
git diff --check
git ls-files --eol -- '*.sh'
release=$(git rev-parse --short=12 HEAD)
qa=$(mktemp -d)
chmod 700 "$qa"
git archive HEAD | tar -x -C "$qa"
cd "$qa"
unset COMPOSE_FILE COMPOSE_ENV_FILES COMPOSE_PROFILES
export COMPOSE_PROJECT_NAME="mallbyte-qa-$release-$$"
export FRONTEND_PORT=3300 API_PORT=8800
python3 scripts/init-dev-env.py
docker compose config --quiet
docker compose up -d --build --wait --wait-timeout 300
docker compose ps --all
```

### Django, production settings and workers

```bash
docker compose exec -T web python manage.py check
docker compose exec -T web python manage.py makemigrations --check --dry-run
docker compose exec -T web python manage.py spectacular --validate --fail-on-warn --file /tmp/schema.yaml
docker compose exec -T web python manage.py test --noinput
docker compose exec -T web ruff check apps config
docker compose exec -T web python -m compileall -q apps config deploy ops
docker compose exec -T web python -B -m unittest ops.tests.test_postgres_client_check -v
docker compose exec -T web python -B -m unittest discover -s deploy/observability_ops -p 'test_*.py' -v
docker compose exec -T redis redis-cli ping
docker compose exec -T web python manage.py shell -c 'from config.celery import debug_task; result = debug_task.delay(); result.get(timeout=45); assert result.successful()'
docker compose exec -T web python manage.py shell -c 'from django_celery_beat.models import PeriodicTask; assert PeriodicTask.objects.filter(name="expire-pending-order-reservations", enabled=True).exists()'
```

### Next.js and HTTP integration

```bash
docker compose run --rm --no-deps frontend sh -ec 'npm run test:run && npm run lint && npm run build && npm run typecheck'
curl -fsS "http://localhost:$API_PORT/healthz/"
curl -fsS -D "$qa/catalog.headers" -o /dev/null -H "Origin: http://localhost:$FRONTEND_PORT" "http://localhost:$API_PORT/api/products/products/"
tr -d '\r' < "$qa/catalog.headers" | grep -Fix "access-control-allow-origin: http://localhost:$FRONTEND_PORT"
tr -d '\r' < "$qa/catalog.headers" | grep -Fix 'access-control-allow-credentials: true'
test "$(curl -sS -o /dev/null -w '%{http_code}' "http://localhost:$API_PORT/api/auth/profile/")" = 401
docker compose exec -T web python manage.py shell -c 'from apps.content.models import ContentPage; ContentPage.objects.update_or_create(slug="qa-integration", defaults={"title":"Local QA","content":"mallbyte-ssr-verified","status":"published"})'
curl -fsS "http://localhost:$FRONTEND_PORT/pages/qa-integration" -o "$qa/cms.html"
grep -Fq 'mallbyte-ssr-verified' "$qa/cms.html"
```

Run the mocked Chromium suite inside an isolated Node 22 container. Its Next.js server uses its own port 3000.

```bash
docker run --rm --init --shm-size=1g -v "$qa/frontend:/source:ro" node:22-bookworm bash -ec '
  mkdir /tmp/mallbyte-web && cd /tmp/mallbyte-web
  cp -a /source/. .
  npm ci
  npm audit
  npm install --prefix .playwright-runner --no-save @playwright/test@1.58.2
  ln -s ../.playwright-runner/node_modules/@playwright node_modules/@playwright
  .playwright-runner/node_modules/.bin/playwright install --with-deps chromium
  CI=true .playwright-runner/node_modules/.bin/playwright test
'
```

### Both production images and retained manifests

This build uses a reserved example HTTPS hostname. The checkout's production workflow builds the frontend with the real public origin only for an approved release.

```bash
cd "$qa"
docker build -f frontend/Dockerfile.production --build-arg NEXT_PUBLIC_API_BASE_URL=https://api.example.test -t "mallbyte-frontend-qa:$release" frontend
docker build -f backend/Dockerfile.production -t "mallbyte-backend-qa:$release" backend
test "$(docker image inspect "mallbyte-frontend-qa:$release" --format '{{.Config.User}}')" = nextjs
test "$(docker image inspect "mallbyte-backend-qa:$release" --format '{{.Config.User}}')" = app
docker run --rm --entrypoint node "mallbyte-frontend-qa:$release" -e "require('fs').accessSync('server.js')"
docker run --rm --env-file .env -e MFA_FERNET_KEY="$(python3 -c 'import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())')" -e ALLOWED_HOSTS=api.example.test,127.0.0.1 -e CACHE_REDIS_URL=redis://localhost:6379/1 "mallbyte-backend-qa:$release" python manage.py check --deploy --fail-level WARNING
(
  cd backend
  cp .env.docker.example .env.docker
  cp .env.staging.example .env.staging
  cp .env.production.example .env.production
  docker compose -f compose.yaml config --quiet
  docker compose -f compose.yaml -f compose.dev.yaml --profile celery --profile tools config --quiet
  docker compose -f compose.staging.yaml config --quiet
  docker compose -f compose.production.yaml config --quiet
  docker run --rm --add-host web:127.0.0.1 -v "$PWD/deploy/nginx/mallbyte-api.conf:/etc/nginx/conf.d/default.conf:ro" nginx:stable-alpine nginx -t
)
NEXT_PUBLIC_API_BASE_URL=https://api.example.test docker compose -f frontend/compose.staging.yaml config --quiet
```

The existing production image workflow additionally starts the standalone web server and polls its HTTP endpoint. To repeat that probe locally:

```bash
preview=$(docker run -d --rm -p 127.0.0.1::3000 "mallbyte-frontend-qa:$release")
preview_address=$(docker port "$preview" 3000/tcp)
curl --retry 15 --retry-delay 2 --retry-connrefused -fsS -o /dev/null "http://$preview_address/"
docker stop "$preview"
```

Run the production Python dependency audit with the repository's security requirements in a separate virtual environment, or repeat it with the CI Python version:

```bash
docker run --rm -v "$qa/backend:/src:ro" -w /src python:3.13-slim sh -ec 'pip install -r requirements/security.txt && pip check && python -m pip_audit --strict --progress-spinner off -r requirements/production.txt'
```

### Stop the verification stack

From `$qa` in the same shell, the following command removes **only the disposable QA project's containers and volumes**, including its synthetic account/content data. Use plain `docker compose down` when stopping your regular development stack.

```bash
cd "$qa"
[[ "$COMPOSE_PROJECT_NAME" == "mallbyte-qa-$release-"* ]]
docker compose -f "$qa/compose.yaml" -p "$COMPOSE_PROJECT_NAME" down --volumes --remove-orphans
cd ~/src/mallbyte
unset COMPOSE_PROJECT_NAME FRONTEND_PORT API_PORT
```

The temporary snapshot and QA image tags are local artifacts. Staging SMTP, external integrations, TLS, backup restoration, secret provisioning and the privileged release hook require a separately provisioned staging environment. [Release and recovery prerequisites](deployment.md)
