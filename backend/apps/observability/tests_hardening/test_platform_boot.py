from datetime import timedelta
from unittest import skipUnless
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from django_celery_beat.models import CrontabSchedule, PeriodicTask

from apps.observability.beat_scheduler import LOCK_KEY, SingletonDatabaseScheduler
from apps.observability.management.commands.setup_observability_scheduler import (
    CLEANUP_TASK,
    HEALTH_TASK,
)
from apps.observability.models import SystemHealthSnapshot


class SchedulerSetupTests(TestCase):
    def test_defaults_are_idempotent(self):
        call_command("setup_observability_scheduler")
        original = list(PeriodicTask.objects.order_by("pk").values())
        call_command("setup_observability_scheduler")
        self.assertEqual(list(PeriodicTask.objects.order_by("pk").values()), original)
        self.assertEqual(PeriodicTask.objects.get(task=HEALTH_TASK).interval.every, 5)
        self.assertEqual(PeriodicTask.objects.get(task=CLEANUP_TASK).interval.every, 24)

    def test_custom_crontab_and_disabled_state_are_preserved(self):
        cron = CrontabSchedule.objects.create(minute="18", hour="3")
        schedule = PeriodicTask.objects.create(
            name="Operator retention", task=CLEANUP_TASK, crontab=cron, enabled=False, kwargs='{"days": 45}'
        )
        before = PeriodicTask.objects.filter(pk=schedule.pk).values().get()
        call_command("setup_observability_scheduler")
        self.assertEqual(PeriodicTask.objects.filter(task=CLEANUP_TASK).values().get(), before)

    def test_duplicate_task_aborts_atomically(self):
        cron = CrontabSchedule.objects.create()
        for name in ("Cleanup A", "Cleanup B"):
            PeriodicTask.objects.create(name=name, task=CLEANUP_TASK, crontab=cron)
        with self.assertRaisesMessage(CommandError, "Conflicting schedules"):
            call_command("setup_observability_scheduler")
        self.assertFalse(PeriodicTask.objects.filter(task=HEALTH_TASK).exists())

    def test_snapshot_probe_requires_fresh_healthy_worker_and_beat(self):
        now = timezone.now()
        fields = {f"{component}_status": "healthy" for component in ("database", "redis", "celery", "celery_beat")}
        snapshot = SystemHealthSnapshot.objects.create(**fields)
        with self.assertRaisesMessage(CommandError, "not ready"):
            call_command("verify_platform_runtime", "snapshot", since=now + timedelta(hours=1))
        call_command("verify_platform_runtime", "snapshot", since=now)
        SystemHealthSnapshot.objects.filter(pk=snapshot.pk).update(celery_status="unhealthy")
        with self.assertRaisesMessage(CommandError, "not ready"):
            call_command("verify_platform_runtime", "snapshot", since=now)

    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL advisory locks")
    def test_beat_excludes_a_second_dispatcher_and_fails_closed_if_lock_is_lost(self):
        first = object.__new__(SingletonDatabaseScheduler)
        second = object.__new__(SingletonDatabaseScheduler)
        with (
            patch("django_celery_beat.schedulers.DatabaseScheduler.setup_schedule"),
            patch("django_celery_beat.schedulers.DatabaseScheduler.tick", return_value=5),
        ):
            first.setup_schedule()
            self.addCleanup(first._lock_session.close)
            self.assertEqual(first.tick(), 5)
            with self.assertRaisesMessage(RuntimeError, "Another Celery Beat"):
                second.setup_schedule()
            with first._lock_session.cursor() as cursor:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [LOCK_KEY])
            with self.assertRaisesMessage(RuntimeError, "lost the database dispatcher lock"):
                first.tick()


    @skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL advisory locks")
    def test_initializer_rejects_concurrent_migrations_and_releases_lock_after_failure(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_lock(746925134)")
            try:
                with self.assertRaisesMessage(CommandError, "Another initialization"):
                    call_command("initialize_platform")
            finally:
                cursor.execute("SELECT pg_advisory_unlock(746925134)")
        with patch("apps.observability.management.commands.initialize_platform.call_command",
                   side_effect=CommandError("migration failed")) as subcommand:
            with self.assertRaisesMessage(CommandError, "migration failed"):
                call_command("initialize_platform")
            subcommand.assert_called_once_with("migrate", interactive=False)
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(746925134)")
            self.assertTrue(cursor.fetchone()[0])
            cursor.execute("SELECT pg_advisory_unlock(746925134)")
