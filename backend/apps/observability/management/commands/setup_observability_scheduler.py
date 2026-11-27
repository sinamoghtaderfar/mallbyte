"""Install the default schedules while retaining operator-managed policy."""
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django_celery_beat.models import IntervalSchedule, PeriodicTask

HEALTH_TASK = "apps.observability.tasks.collect_observability_health_snapshot"
CLEANUP_TASK = "apps.observability.tasks.cleanup_observability_logs"
SCHEDULES = (
    ("Collect observability health snapshot", HEALTH_TASK, 5, IntervalSchedule.MINUTES),
    ("Clean up observability logs", CLEANUP_TASK, 24, IntervalSchedule.HOURS),
)


class Command(BaseCommand):
    help = "Install missing observability schedules and preserve existing settings."

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_xact_lock(746925131)")
        for name, task, every, period in SCHEDULES:
            rows = PeriodicTask.objects.filter(task=task)
            if rows.count() > 1 or PeriodicTask.objects.filter(name=name).exclude(task=task).exists():
                raise CommandError(f"Conflicting schedules for {task}. Review Django periodic tasks.")
            existing = rows.first()
            if existing:
                self.stdout.write(f"Preserved {task} (enabled={existing.enabled}).")
                continue
            interval = IntervalSchedule.objects.filter(every=every, period=period).order_by("pk").first()
            if interval is None:
                interval = IntervalSchedule.objects.create(every=every, period=period)
            PeriodicTask.objects.create(name=name, task=task, interval=interval, args="[]")
            self.stdout.write(f"Installed {task} (every {every} {period}).")
