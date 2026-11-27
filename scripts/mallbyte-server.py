#!/usr/bin/env python3
"""Install boot recovery and roll out one explicitly selected Compose project."""
import argparse
import base64
import binascii
import datetime as dt
import fcntl
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, nullcontext
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/deploy/observability_ops"))
from install_timer import timer_conflicts  # noqa: E402
from managed_compose import DOCKER, managed_compose  # noqa: E402

PROJECTS = {"development": "mallbyte-dev", "staging": "mallbyte-staging", "production": "mallbyte-production"}
SYSTEMD = Path("/etc/systemd/system")
LOCK_DIR = Path.home() / ".cache/mallbyte"
WORKER = "celery_worker"
BEAT = "celery_beat"
PERSISTENT_TARGETS = {
    "db": {"/var/lib/postgresql/data"},
    "redis": {"/data"},
    "web": {"/app/media", "/app/private_media"},
}


class Failure(Exception):
    """An operator-facing error that contains no rendered configuration."""


def execute(argv, step, *, seconds=45, allow_failure=False, cwd=ROOT):
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=seconds, check=False)
    except (OSError, subprocess.TimeoutExpired):
        if allow_failure:
            return None
        raise Failure(f"{step} could not complete within {seconds}s. Check host journal or Docker service logs.") from None
    if result.returncode and not allow_failure:
        raise Failure(f"{step} failed (exit {result.returncode}). Check the corresponding service logs privately.")
    return result


def safe_path(path):
    if not re.fullmatch(r"/[a-zA-Z0-9_./ -]+", str(path)):
        raise Failure("Repository and Python paths must use ordinary ASCII path characters.")
    return path


def check_host():
    if sys.platform != "linux" or not Path("/run/systemd/system").is_dir() or Path("/.dockerenv").exists():
        raise Failure("Use a Linux host booted with systemd and a local system Docker Engine.")
    if os.geteuid() == 0:
        raise Failure("Run as the non-root deployment account with access to Docker.")
    for binary in ("docker", "systemctl"):
        if not shutil.which(binary):
            raise Failure(f"Install {binary} before continuing.")
    result = execute([*DOCKER, "compose", "version", "--short"], "Docker Compose version")
    match = re.match(r"v?(\d+)\.(\d+)", result.stdout.strip())
    if not match or tuple(map(int, match.groups())) < (2, 24):
        raise Failure("Install Docker Compose v2.24 or newer.")
    execute([*DOCKER, "info", "--format", "{{.ServerVersion}}"], "Local Docker access")
    safe_path(ROOT)
    safe_path(Path(sys.executable).resolve())


def env_file(environment):
    return ROOT / (".env" if environment == "development" else f"backend/.env.{environment}")


def https_origin(value):
    try:
        url = urlsplit(value)
        return (url.scheme == "https" and bool(url.hostname) and "*" not in url.hostname and
                not (url.username or url.password or url.path or url.query or url.fragment) and url.port != 0)
    except ValueError:
        return False


def verified_redis(value):
    try:
        url = urlsplit(value)
        return (url.scheme == "rediss" and bool(url.hostname) and
                parse_qs(url.query).get("ssl_cert_reqs", ["required"]) == ["required"])
    except ValueError:
        return False


def validate_config(config, environment):
    services = config["services"]
    local = environment == "development"
    required = {"web", WORKER, BEAT, "frontend"}
    required |= {"db", "redis", "migrate"} if local else {"gateway"}
    if not required.issubset(services):
        raise Failure("Compose is missing required Mallbyte services.")
    for name, spec in services.items():
        if spec.get("privileged") or spec.get("network_mode") == "host" or spec.get("use_api_socket"):
            raise Failure(f"Unsafe container privileges or network configuration in {name}.")
        for mount in spec.get("volumes", []):
            source = Path(mount.get("source") or "/")
            socket = Path("/var/run/docker.sock").resolve()
            if ("docker.sock" in str(source) or "docker.sock" in mount["target"] or
                    (mount.get("type") == "bind" and source.resolve() in socket.parents)):
                raise Failure(f"Docker socket mount rejected in {name}.")
        if name in PERSISTENT_TARGETS:
            persisted = {m["target"] for m in spec.get("volumes", []) if m.get("source")}
            if not PERSISTENT_TARGETS[name].issubset(persisted):
                raise Failure(f"Explicit persistent storage is required for {name}.")
        for port in spec.get("ports", []):
            if port.get("host_ip") not in {"127.0.0.1", "::1"}:
                raise Failure(f"Published ports for {name} must bind to loopback.")
        if name in required:
            expected = "no" if name == "migrate" else "unless-stopped"
            if spec.get("restart", "no") != expected:
                raise Failure(f"Incorrect restart policy for {name}.")
    beat = services[BEAT]
    if beat.get("scale", 1) != 1 or beat.get("deploy", {}).get("replicas", 1) != 1:
        raise Failure("Celery Beat must be a singleton.")
    role_names = ("web", WORKER, BEAT, "migrate") if local else ("web", WORKER, BEAT)
    link_fields = ("DB_ENGINE", "DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD",
                   "DB_SSLMODE", "CACHE_REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
    web_env = services["web"].get("environment", {})
    for name in role_names:
        app = services[name].get("environment", {})
        secret = app.get("SECRET_KEY") or ""
        database_password = app.get("DB_PASSWORD") or ""
        if not secret or not database_password:
            raise Failure(f"Set SECRET_KEY and DB_PASSWORD for {name}.")
        if any(app.get(key) != web_env.get(key) for key in link_fields):
            raise Failure(f"Database or Redis settings in {name} differ from web.")
        if local:
            if app.get("DB_HOST") != "db" or app.get("DJANGO_SETTINGS_MODULE") != "config.settings.base":
                raise Failure(f"{name} must use the local database and development settings.")
            continue
        missing = [key for key in ("DB_HOST", "DB_NAME", "DB_USER", "CACHE_REDIS_URL", "CELERY_BROKER_URL",
                   "CELERY_RESULT_BACKEND", "EMAIL_HOST", "EMAIL_HOST_USER", "EMAIL_HOST_PASSWORD",
                   "DEFAULT_FROM_EMAIL") if not app.get(key) or str(app[key]).startswith(("YOUR_", "REPLACE_WITH_"))]
        if missing:
            raise Failure(f"Missing or placeholder configuration for {name}: {', '.join(missing)}.")
        try:
            mfa_key = base64.b64decode(app.get("MFA_FERNET_KEY", ""), altchars=b"-_", validate=True)
        except (ValueError, binascii.Error):
            mfa_key = b""
        if (app.get("DJANGO_SETTINGS_MODULE") != "config.settings.production" or
                app.get("DEBUG", "").lower() != "false" or app.get("CACHE_BACKEND") != "redis" or
                len(secret) < 50 or secret.startswith(("django-insecure", "REPLACE_WITH_")) or
                database_password.startswith("REPLACE_WITH_") or len(mfa_key) != 32 or
                app.get("MFA_FERNET_KEY") == secret or
                app.get("SECURE_SSL_REDIRECT", "true").lower() != "true" or
                app.get("DB_ENGINE", "django.db.backends.postgresql") != "django.db.backends.postgresql" or
                app.get("DB_SSLMODE", "require") not in {"require", "verify-full", "verify-ca"}):
            raise Failure(f"Secure Django, database, secret and MFA settings are required for {name}.")
        hosts = [part.strip() for part in app.get("ALLOWED_HOSTS", "").split(",")]
        if "127.0.0.1" not in hosts or not any(host not in {"", "127.0.0.1", "localhost", "[::1]"} for host in hosts) or any("*" in host or host.startswith(".") for host in hosts):
            raise Failure("Set exact API ALLOWED_HOSTS plus 127.0.0.1 for the internal probe.")
        for key in ("CSRF_TRUSTED_ORIGINS", "CORS_ALLOWED_ORIGINS"):
            if not all(https_origin(value.strip()) for value in app.get(key, "").split(",")):
                raise Failure(f"Set explicit HTTPS origins in {key}.")
        if app.get("EMAIL_BACKEND") != "django.core.mail.backends.smtp.EmailBackend" or not any(
            app.get(key, "").lower() == "true" for key in ("EMAIL_USE_TLS", "EMAIL_USE_SSL")
        ) or all(app.get(key, "").lower() == "true" for key in ("EMAIL_USE_TLS", "EMAIL_USE_SSL")):
            raise Failure("Configure SMTP with either TLS or SSL.")
        if environment == "production" and any(
            not verified_redis(app[key]) for key in ("CACHE_REDIS_URL", "CELERY_BROKER_URL", "CELERY_RESULT_BACKEND")
        ):
            raise Failure("Production Redis endpoints must use rediss:// with certificate validation.")
    if not local and not https_origin(services["frontend"]["build"]["args"]["NEXT_PUBLIC_API_BASE_URL"]):
        raise Failure("Set NEXT_PUBLIC_API_BASE_URL to one HTTPS API origin.")


def preflight(args):
    check_host()
    path = env_file(args.environment)
    try:
        mode = path.stat()
        if not stat.S_ISREG(mode.st_mode) or mode.st_mode & 0o077 or not os.access(path, os.R_OK):
            raise Failure(f"Use a readable regular file at {path} with permissions 0600.")
    except FileNotFoundError:
        raise Failure(f"Supply {path}. For development run python3 scripts/init-dev-env.py.") from None
    compose = managed_compose(ROOT, args.environment, args.project)
    config = execute([*compose, "config", "--format", "json"], "Compose configuration validation")
    try:
        validate_config(json.loads(config.stdout), args.environment)
    except (KeyError, ValueError, TypeError):
        raise Failure("Compose returned an invalid Mallbyte service configuration.") from None
    return compose, json.loads(config.stdout)


@contextmanager
def deployment_lock(project):
    LOCK_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    # A managed rollout and its host executor must serialize for this project.
    path = LOCK_DIR / f"deployment-{project}.lock"
    with path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure("Another deployment or the observability executor holds the host lock. Retry shortly.") from None
        yield


def assert_project(compose, project, config):
    result = execute([*DOCKER, "ps", "-a", "--filter", f"label=com.docker.compose.project={project}",
                      "--format", '{{.ID}}|{{.Label "com.docker.compose.service"}}|'
                      '{{.Label "com.docker.compose.project.working_dir"}}|{{.Label "com.docker.compose.project.config_files"}}|'
                      '{{.Label "com.docker.compose.container-number"}}'],
                     "Compose project ownership check")
    directory = compose[compose.index("--project-directory") + 1]
    manifest = compose[compose.index("-f") + 1]
    beats = 0
    for line in result.stdout.splitlines():
        container, service, path, manifests, ordinal = line.split("|", maxsplit=4)
        # A standalone docker run can inherit project and service labels from
        # a Compose-built image. Only Compose adds the checkout and ordinal.
        if not path and not manifests and not ordinal:
            continue
        if Path(path) != Path(directory) or manifest not in manifests.split(","):
            raise Failure("The project name belongs to another checkout or environment. Confirm project identity before migrating.")
        beats += service == BEAT
        if service not in PERSISTENT_TARGETS:
            continue
        targets = PERSISTENT_TARGETS[service]
        current = execute([*DOCKER, "inspect", "--format", "{{json .Mounts}}", container],
                          f"Persistent storage identity for {service}")
        old = {m["Destination"]: m.get("Name") or m["Source"] for m in json.loads(current.stdout)
               if m["Destination"] in targets}
        new = {m["target"]: config["volumes"][m["source"]]["name"] if m["type"] == "volume" else m["source"]
               for m in config["services"][service].get("volumes", []) if m["target"] in targets}
        if old != new:
            raise Failure(f"Persistent mounts would change for {service}. Review storage migration separately.")
    if beats > 1:
        raise Failure("More than one Celery Beat container exists for this project. Reconcile the scheduler before continuing.")


def wait_for(command, step, *, seconds):
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise Failure(f"{step} did not become ready within {seconds}s. Check the service logs.")
        probe = execute(command, step, seconds=min(remaining, 20), allow_failure=True)
        if probe is not None and probe.returncode == 0:
            print(f"{step}: ready.", flush=True)
            return
        time.sleep(min(3, max(end - time.monotonic(), 0)))


def rollout(args, compose, config):
    local = args.environment == "development"
    boot = args.action == "boot"

    def run(step, *command, seconds=180):
        print(step, flush=True)
        return execute([*compose, *command], step, seconds=seconds)

    up = ["up", "-d", "--no-deps", "--no-build", "--pull", "never", "--wait", "--wait-timeout", "170"]
    with deployment_lock(args.project):
        assert_project(compose, args.project, config)
        if not boot:
            if local:
                run("Retrieve development database and cache images", "pull", "db", "redis", seconds=600)
            elif args.pull:
                run("Retrieve release images", "pull", "web", "frontend", "gateway", seconds=900)
            else:
                run("Retrieve gateway image", "pull", "gateway", seconds=600)
            if not args.pull:
                run("Build release images", "build", "migrate" if local else "web", "frontend", seconds=900)
        if not boot:
            run("Pause the scheduler during the migration", "stop", BEAT)
        if local:
            run("Start PostgreSQL and Redis", *up, "--no-recreate", "db", "redis")
            run("Apply migrations and initialize observability", "run", "--rm", "--no-deps", "--pull", "never", "migrate", seconds=600)
        else:
            remote = [*compose, "run", "--rm", "--no-deps", "--pull", "never", "web"]
            wait_for([*remote, "python", "manage.py", "verify_platform_runtime", "dependencies"],
                     "External PostgreSQL, Redis broker and cache", seconds=175)
            run("Validate production Django settings and initialize release",
                *remote[len(compose):], "python", "manage.py", "initialize_platform", "--remote", seconds=600)
        if boot:
            up += ["--no-recreate"]
        run("Start Django", *up, "web")
        run("Start Celery Worker", *up, WORKER)
        verify = [*compose, "exec", "-T", "web", "python", "manage.py", "verify_platform_runtime"]
        wait_for([*verify, "worker"], "Celery Worker task delivery", seconds=60)
        since = dt.datetime.now().astimezone().isoformat()
        run("Start singleton Celery Beat", *up, BEAT)
        run("Start storefront", *up, *(("frontend",) if local else ("gateway", "frontend")))
        if args.snapshot_timeout:
            wait_for([*verify, "snapshot", "--since", since], "Fresh scheduled health snapshot",
                     seconds=args.snapshot_timeout)
    print("Mallbyte startup verified.", flush=True)


def system_unit(args):
    return (ROOT / "deploy/systemd/mallbyte.service.in").read_text().replace(
        "@ENVIRONMENT@", args.environment,
    ).replace("@ROOT@", str(ROOT)).replace("@USER@", args.user).replace(
        "@PYTHON@", str(Path(sys.executable).resolve()),
    ).replace("@PROJECT@", args.project).replace("@SNAPSHOT_TIMEOUT@", str(args.snapshot_timeout))


def unit_paths(args):
    return SYSTEMD / f"{args.project}.service", system_unit(args)


def reconcile_unit(args):
    target, desired = unit_paths(args)
    # This never silently replaces the pre-existing mallbyte-dev host service.
    if target.is_symlink() or (target.exists() and target.read_text() != desired):
        raise Failure(f"{target.name} has a different configuration. Follow the documented adoption procedure.")
    timer_env = "monorepo" if args.environment == "development" else args.environment
    timer = [sys.executable, str(ROOT / "backend/deploy/observability_ops/install_timer.py"),
             "--environment", timer_env, "--project", args.project]
    if args.action == "install":
        conflicts = timer_conflicts(timer_env, args.project, execute)
        if conflicts:
            raise Failure("Disable duplicate executor timers before installation: " + ", ".join(conflicts))
        execute([*timer, "--install", "--dry-run"], "Validate executor timer installation")
        if not args.apply:
            print(desired)
            print("Plan: enable Docker and the boot service, then install the per-user executor timer.")
            if args.linger:
                print(f"Plan: enable systemd linger for {args.user}.")
            return
        if not target.exists():
            with tempfile.TemporaryDirectory() as tmp:
                unit = Path(tmp, target.name)
                unit.write_text(desired)
                execute(["sudo", "install", "-m", "0644", str(unit), str(target)], "Install boot unit")
        execute(["sudo", "systemctl", "daemon-reload"], "Reload systemd units")
        execute(["sudo", "systemctl", "enable", "docker.service", target.name], "Enable boot services")
        if args.linger:
            execute(["sudo", "loginctl", "enable-linger", args.user], "Enable user manager persistence")
        execute([*timer, "--install"], "Install user executor timer")
        print("Boot service enabled. Start it on the next approved maintenance window or use deploy for a release.")
    elif args.apply:
        if target.exists():
            execute(["sudo", "systemctl", "disable", target.name], "Disable boot service")
            execute(["sudo", "rm", "--", str(target)], "Remove managed boot unit")
            execute(["sudo", "systemctl", "daemon-reload"], "Reload systemd units")
        execute([*timer, "--uninstall"], "Remove user executor timer")
        print("Removed managed boot configuration. The Docker stack is still available.")
    else:
        print(f"Plan: disable and remove {target.name} and its managed executor timer.")


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "install", "uninstall", "boot", "deploy", "stop"))
    parser.add_argument("--environment", required=True, choices=PROJECTS)
    parser.add_argument("--project", help="Compose project name and system unit basename")
    parser.add_argument("--user", default=pwd.getpwuid(os.getuid()).pw_name)
    parser.add_argument("--apply", action="store_true", help="Authorize install, uninstall or stop")
    parser.add_argument("--linger", action="store_true", help="Explicitly authorize enabling user manager at boot")
    parser.add_argument("--pull", action="store_true", help="Pull supplied staging/production images instead of building")
    parser.add_argument("--snapshot-timeout", type=int, default=660, help="Maximum seconds to await a scheduler snapshot, or 0 to defer")
    args = parser.parse_args(argv)
    args.project = args.project or PROJECTS[args.environment]
    if not re.fullmatch(r"mallbyte-[a-z0-9][a-z0-9_-]{0,47}", args.project) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", args.user):
        parser.error("Invalid Mallbyte project or Linux user name.")
    if args.user != pwd.getpwuid(os.getuid()).pw_name:
        parser.error("Invoke this command as the configured deployment user.")
    if args.linger and args.action != "install":
        parser.error("--linger is only valid with install.")
    if args.apply and args.action not in {"install", "uninstall", "stop"}:
        parser.error("--apply is only valid with install, uninstall or stop.")
    if args.pull and (args.action != "deploy" or args.environment == "development"):
        parser.error("--pull is only valid for staging or production deployments.")
    if not 0 <= args.snapshot_timeout <= 900:
        parser.error("Snapshot timeout must be between 0 and 900 seconds.")
    return args


def main(argv=None):
    args = arguments(argv)
    try:
        compose, config = preflight(args) if args.action != "uninstall" else (None, None)
        if args.action in {"install", "uninstall"}:
            with deployment_lock(args.project) if args.apply else nullcontext():
                if compose:
                    assert_project(compose, args.project, config)
                reconcile_unit(args)
        elif args.action in {"boot", "deploy"}:
            rollout(args, compose, config)
        elif args.action == "stop" and args.apply:
            with deployment_lock(args.project):
                assert_project(compose, args.project, config)
                execute([*compose, "stop"], "Stop Mallbyte services", seconds=180)
        else:
            assert_project(compose, args.project, config)
            print(f"Plan verified for {args.project} ({args.environment}). Docker, Compose, configuration and loopback ports are valid.")
            print("Readiness order: dependencies, migrations, scheduler setup, Django, worker, Beat, frontend, fresh snapshot.")
            if args.action == "stop":
                print("Plan: stop containers and retain volumes. Disable the boot unit separately for maintenance across reboots.")
    except (Failure, OSError, UnicodeError) as exc:
        print(f"Mallbyte: {exc if isinstance(exc, Failure) else 'Host file or permission check failed.'}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
