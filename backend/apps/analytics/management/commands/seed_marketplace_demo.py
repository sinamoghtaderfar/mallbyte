"""Manual, development-only installation of the independent print marketplace."""

import hashlib
import json
import os
from datetime import date, datetime, time

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from apps.analytics.marketplace_demo.fixture import Fixture, preflight
from apps.analytics.marketplace_demo.ownership import Inventory, cleanup, remove_files
from apps.analytics.marketplace_demo.plan import DEFAULTS, Plan
from apps.analytics.models import MarketplaceDemoRun


def environment():
    env = os.environ
    if settings.SETTINGS_MODULE != "config.settings.base" or not settings.DEBUG:
        raise CommandError("Only the DEBUG development base settings may run this command.")
    if env.get("MARKETPLACE_DEMO_ENV") not in {"development", "demo"}:
        raise CommandError("Set MARKETPLACE_DEMO_ENV to development or demo.")
    if connection.vendor != "postgresql":
        raise CommandError("The marketplace fixture requires PostgreSQL.")
    expected = env.get("MARKETPLACE_DEMO_DATABASE")
    if not expected or expected != connection.settings_dict["NAME"]:
        raise CommandError(
            "MARKETPLACE_DEMO_DATABASE must match the selected development database."
        )
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_database()")
        if cursor.fetchone()[0] != expected:
            raise CommandError("Connected database differs from the explicit demo target.")


def required(name):
    value = os.environ.get(name, "").strip().lower()
    if not value:
        raise CommandError(f"Set {name} locally.")
    return value


def checked_email(value):
    try:
        validate_email(value)
    except ValidationError:
        raise CommandError(
            "Invalid email configuration. Check the local identity variables."
        ) from None
    return value


def verify_manifest(run):
    for label, ids in run.rows.items():
        if apps.get_model(label)._base_manager.filter(pk__in=ids).count() != len(ids):
            raise CommandError(
                f"Demo records have changed ({label}). Inspect the fixture before rerunning."
            )
    if any(not default_storage.exists(path) for path in run.files):
        raise CommandError("Demo media is missing. Inspect the fixture before rerunning.")


class Command(BaseCommand):
    help = "Plan, install, or remove the development print marketplace. No deployment hook."
    requires_system_checks = []

    def add_arguments(self, parser):
        for name, default in DEFAULTS.items():
            parser.add_argument(f"--{name}", type=int, default=default)
        parser.add_argument("--seed", type=int, default=314159)
        parser.add_argument(
            "--as-of", type=date.fromisoformat, help="Last day of the simulation (YYYY-MM-DD)."
        )
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--cleanup", action="store_true")

    def handle(self, *args, **opts):
        environment()
        protected = checked_email(required("MARKETPLACE_DEMO_SUPERUSER_EMAIL"))
        inv = Inventory()
        try:
            with transaction.atomic():
                # Serializes even the first run when there is no manifest row to lock.
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(584230191)")
                # A read-only preview also works before the provenance migration.
                migrated = (
                    MarketplaceDemoRun._meta.db_table in connection.introspection.table_names()
                )
                if not migrated and not opts["dry_run"]:
                    raise CommandError(
                        "Apply the analytics provenance migration in the development database first."
                    )
                run = MarketplaceDemoRun.objects.filter(pk=1).first() if migrated else None
                if opts["cleanup"]:
                    report = {"action": "empty", "rows": {}}
                    if run:
                        report = {
                            "action": "would remove" if opts["dry_run"] else "removed",
                            "rows": {k: len(v) for k, v in run.rows.items()},
                            "media": len(run.files),
                        }
                        if not opts["dry_run"]:
                            cleanup(run, protected)
                else:
                    report = self.seed(opts, protected, inv, run)
        except IntegrityError:
            remove_files(inv.files)
            raise CommandError(
                "An existing key or dependent record conflicts with the fixture. Database changes rolled back."
            ) from None
        except BaseException:
            remove_files(inv.files)
            raise
        self.stdout.write(json.dumps(report, indent=2, sort_keys=True))

    def seed(self, opts, protected, inv, run):
        sizes = {k: opts[k] for k in DEFAULTS}
        day = opts["as_of"] or (run.as_of if run else timezone.localdate())
        if day > timezone.localdate():
            raise CommandError("The history end date cannot be in the future.")
        prefix = required("MARKETPLACE_DEMO_SELLER_PREFIX")
        domain = required("MARKETPLACE_DEMO_SELLER_DOMAIN")
        emails = [checked_email(f"{prefix}{i + 1}@{domain}") for i in range(sizes["sellers"])]
        spec = [1, str(day), sizes, opts["seed"], prefix, domain, protected]
        fingerprint = hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()
        if run:
            if run.fingerprint != fingerprint:
                raise CommandError(
                    "A different fixture is installed. Preview demo-only cleanup before replacing it."
                )
            if not run.rows:
                raise CommandError("Demo media cleanup is pending. Retry --cleanup.")
            verify_manifest(run)
            return {"action": "unchanged", "creates": 0, "dataset": run.report}
        end = timezone.make_aware(datetime.combine(day, time.min))
        plan = Plan(sizes, opts["seed"], end)
        existing = preflight(emails, protected, sizes, dry_run=opts["dry_run"])
        report = dict(
            plan.report,
            store_profiles_created=len(emails) - len(existing),
            store_profiles_reused=len(existing),
            seller_accounts_created=len(emails) - len(existing),
            seller_accounts_reused=len(existing),
            as_of=str(day),
            seed=opts["seed"],
        )
        if opts["dry_run"]:
            return {"action": "would create", "dataset": report}
        fixture = Fixture(inv, plan, opts["seed"], end)
        fixture.populate(emails, existing)
        MarketplaceDemoRun.objects.create(
            pk=1,
            fingerprint=fingerprint,
            as_of=day,
            rows=dict(inv.rows),
            files=inv.files,
            reused_stores=list(fixture.reused.values()),
            report=report,
        )
        return {
            "action": "created",
            "dataset": report,
            "rows": {k: len(v) for k, v in inv.rows.items()},
            "media": len(inv.files),
        }
