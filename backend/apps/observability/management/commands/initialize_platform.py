"""Serialize migrations and initialization even when deployment clients disconnect."""
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Apply migrations and observability defaults under a PostgreSQL deployment lock."

    def add_arguments(self, parser):
        parser.add_argument("--remote", action="store_true", help="Also check deployment settings and collect static assets.")

    def handle(self, *args, **options):
        if connection.vendor != "postgresql":
            raise CommandError("Initialization requires PostgreSQL.")
        lock = connection.copy(alias="platform_initialization_lock")
        try:
            with lock.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(746925134)")
                if not cursor.fetchone()[0]:
                    raise CommandError("Another initialization owns this database. Wait for it to finish.")
            if options["remote"]:
                call_command("check", deploy=True, fail_level="ERROR")
            call_command("migrate", interactive=False)
            call_command("setup_observability_scheduler")
            if options["remote"]:
                call_command("collectstatic", interactive=False)
        finally:
            lock.close()
