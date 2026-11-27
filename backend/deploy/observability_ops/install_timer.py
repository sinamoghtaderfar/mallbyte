#!/usr/bin/env python3
"""Install a per-user timer on the trusted host."""
import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
USER_DIR = Path.home() / ".config/systemd/user"
PREFIX = "mallbyte-observability-ops"
ENVIRONMENTS = ("monorepo", "development", "staging", "production")


def execute(command, step):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=30, check=True)
    except (OSError, subprocess.SubprocessError):
        raise SystemExit(f"{step} failed. Check the deployment user's systemd manager.") from None


def timer_conflicts(environment, project, run=execute):
    names = {f"{PREFIX}.timer", f"{PREFIX}-{environment}.timer"}
    if environment in {"monorepo", "development"}:
        names.update({f"{PREFIX}-monorepo.timer", f"{PREFIX}-development.timer"})
    configured = f"{PREFIX}-{project or environment}.timer"
    # Flag managed timers that use the same environment, even with a different project name.
    for path in USER_DIR.glob(f"{PREFIX}-*.service"):
        command = next((line for line in path.read_text().splitlines() if line.startswith("ExecStart=")), "")
        if re.search(rf" --environment {environment}( |$)", command):
            names.add(path.with_suffix(".timer").name)
    names.discard(configured)
    files = run(["systemctl", "--user", "list-unit-files", f"{PREFIX}*.timer", "--no-legend", "--no-pager"],
                "List executor timer enablement")
    active = run(["systemctl", "--user", "list-units", "--type=timer", f"{PREFIX}*.timer", "--no-legend", "--no-pager"],
                 "List active executor timers")
    enabled = {fields[0] for line in files.stdout.splitlines()
               if len(fields := line.split()) >= 2 and fields[1] in {"enabled", "enabled-runtime", "linked", "linked-runtime", "alias"}}
    running = {fields[0] for line in active.stdout.splitlines() if (fields := line.split())}
    return sorted(names & (enabled | running))


def units(environment, project):
    suffix = project or environment
    service = f"{PREFIX}-{suffix}.service"
    timer = f"{PREFIX}-{suffix}.timer"
    root = str(ROOT)
    python = str(Path(sys.executable).resolve())
    if any(not re.fullmatch(r"/[a-zA-Z0-9_./ -]+", path) for path in (root, python)):
        raise SystemExit("Repository and Python paths must use ordinary ASCII path characters.")
    extra = f" --project {project}" if project else ""
    return {
        service: f'''[Unit]
Description=Mallbyte independently approved infrastructure executor

[Service]
Type=oneshot
TimeoutStartSec=300
WorkingDirectory={root}
ExecStart="{python}" -B "{root}/deploy/observability_ops/host_runner.py" --environment {environment}{extra}
Environment=PATH=/usr/local/bin:/usr/bin:/bin
NoNewPrivileges=true
''',
        timer: f'''[Unit]
Description=Poll independently approved Mallbyte Celery restart requests

[Timer]
OnBootSec=1min
OnUnitInactiveSec=1min
Unit={service}

[Install]
WantedBy=timers.target
''',
    }


def install_units(action, environment, project, *, dry_run):
    desired = units(environment, project)
    timer = next(name for name in desired if name.endswith(".timer"))
    if action == "install":
        conflicts = timer_conflicts(environment, project)
        if conflicts:
            raise SystemExit("Disable duplicate executor timers before installation: " + ", ".join(conflicts))
    for name, content in desired.items():
        path = USER_DIR / name
        if path.is_symlink() or (path.exists() and path.read_text() != content):
            raise SystemExit(f"{name} already exists with different contents. Back it up and review the timer adoption runbook.")
    if dry_run:
        print(f"Plan: {action} {timer}.")
        return
    if action == "install":
        USER_DIR.mkdir(parents=True, exist_ok=True)
        for name, content in desired.items():
            path = USER_DIR / name
            if path.exists():
                continue
            with tempfile.NamedTemporaryFile(dir=USER_DIR, mode="w", delete=False) as unit:
                unit.write(content)
            os.chmod(unit.name, 0o644)
            os.replace(unit.name, path)
        execute(["systemctl", "--user", "daemon-reload"], "Reload user units")
        execute(["systemctl", "--user", "enable", "--now", timer], "Enable executor timer")
    else:
        if (USER_DIR / timer).exists():
            execute(["systemctl", "--user", "disable", "--now", timer], "Disable executor timer")
        for name in desired:
            (USER_DIR / name).unlink(missing_ok=True)
        execute(["systemctl", "--user", "daemon-reload"], "Reload user units")
    print(f"{timer}: {action} complete.")


def main():
    if Path("/.dockerenv").exists() or os.geteuid() == 0:
        raise SystemExit("Run as the deployment user on the Docker host.")
    parser = argparse.ArgumentParser()
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--install", action="store_true")
    action.add_argument("--uninstall", action="store_true")
    parser.add_argument("--environment", required=True, choices=ENVIRONMENTS)
    parser.add_argument("--project", help="Use the host bootstrap's explicitly selected Compose project")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.project and (args.environment == "development" or not re.fullmatch(r"mallbyte-[a-z0-9][a-z0-9_-]{0,47}", args.project)):
        parser.error("Invalid managed project or environment.")
    install_units("install" if args.install else "uninstall", args.environment, args.project, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
