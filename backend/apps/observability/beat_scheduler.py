"""DatabaseScheduler with one dispatcher per PostgreSQL database."""
from django.db import connection
from django_celery_beat.schedulers import DatabaseScheduler

LOCK_KEY = 746925132


class SingletonDatabaseScheduler(DatabaseScheduler):
    def setup_schedule(self):
        if connection.vendor != "postgresql":
            raise RuntimeError("Mallbyte's singleton scheduler requires PostgreSQL.")
        # Use a dedicated session so Django's normal connection lifecycle cannot
        # release the dispatcher lock while Celery is publishing messages.
        self._lock_session = connection.copy(alias="beat_dispatch_lock")
        try:
            with self._lock_session.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [LOCK_KEY])
                if not cursor.fetchone()[0]:
                    raise RuntimeError("Another Celery Beat holds the database dispatcher lock.")
            super().setup_schedule()
        except BaseException:
            self._lock_session.close()
            raise

    def tick(self, *args, **kwargs):
        with self._lock_session.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' "
                "AND pid = pg_backend_pid() AND classid = 0 AND objid = %s AND objsubid = 1 AND granted)",
                [LOCK_KEY],
            )
            if not cursor.fetchone()[0]:
                raise RuntimeError("Celery Beat lost the database dispatcher lock.")
        return super().tick(*args, **kwargs)

    def close(self):
        try:
            super().close()
        finally:
            if hasattr(self, "_lock_session"):
                self._lock_session.close()
