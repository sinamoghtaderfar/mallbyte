import logging

from celery import shared_task
from django.utils import timezone

from .mfa_models import AdminMFAChallenge, SecurityNotice
from .security_mail import deliver


@shared_task
def retry_security_notices():
    AdminMFAChallenge.objects.filter(expires_at__lte=timezone.now()).exclude(pending_password="").update(pending_password="")
    for pk in SecurityNotice.objects.filter(sent_at=None).order_by("created_at").values_list("pk", flat=True)[:100]:
        try:
            deliver(pk)
        except OSError:
            # SMTP errors inherit OSError. Keep other recipients progressing.
            logging.getLogger(__name__).warning("Security notice %s remains pending", pk)
