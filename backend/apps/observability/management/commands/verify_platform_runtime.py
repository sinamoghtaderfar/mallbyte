"""Readiness probes for the host deployment command."""
from datetime import datetime

from django.conf import settings
from django.core.cache import caches
from django.core.management.base import BaseCommand, CommandError

from apps.observability.models import SystemHealthSnapshot
from apps.observability.services import check_database_health, check_redis_health
from config.celery import debug_task


class Command(BaseCommand):
    help = "Verify dependency access, real Celery delivery, or a recent scheduler snapshot."

    def add_arguments(self, parser):
        parser.add_argument("probe", choices=("dependencies", "worker", "snapshot"))
        parser.add_argument("--since", type=datetime.fromisoformat)

    def handle(self, *args, **options):
        probe = options["probe"]
        try:
            if probe == "dependencies":
                healthy = all(check()["status"] == "healthy" for check in (
                    check_database_health, check_redis_health,
                ))
                if healthy and "django_redis" in settings.CACHES["default"]["BACKEND"]:
                    healthy = bool(caches["default"].client.get_client(write=True).ping())
            elif probe == "worker":
                result = debug_task.delay()
                result.get(timeout=12)
                healthy = result.successful()
                result.forget()
            else:
                if options["since"] is None:
                    raise CommandError("The snapshot probe requires --since.")
                snapshot = SystemHealthSnapshot.objects.filter(
                    created_at__gte=options["since"],
                ).order_by("-created_at").first()
                healthy = snapshot and all(
                    getattr(snapshot, field) == "healthy"
                    for field in ("database_status", "redis_status", "celery_status", "celery_beat_status")
                )
        except CommandError:
            raise
        except Exception:
            healthy = False
        if not healthy:
            raise CommandError(f"{probe} is not ready.")
        self.stdout.write(f"{probe} is ready.")
