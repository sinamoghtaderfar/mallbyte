"""Transactional security notices, with a durable retry queue."""
from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.db import transaction
from django.utils import timezone

from .mfa_models import SecurityNotice


def queue_notice(user, event):
    if user.email_verified:
        notice = SecurityNotice.objects.create(recipient=user.email, event=event)
        transaction.on_commit(lambda: deliver(notice.pk), robust=True)


@transaction.atomic
def deliver(pk):
    notice = SecurityNotice.objects.select_for_update().filter(pk=pk, sent_at=None).first()
    if not notice:
        return
    message = EmailMessage(
        "Mallbyte account security update",
        f"Mallbyte recorded this security event at {notice.created_at:%Y-%m-%d %H:%M %Z}:\n\n"
        f"{notice.event}\n\nSign in to Mallbyte to review your access. For first-time privileged "
        "enrollment contact your administrator through a known independent channel. "
        "If you did not expect this change, contact your security team immediately.",
        settings.DEFAULT_FROM_EMAIL, [notice.recipient],
        headers={"Message-ID": f"<security-{notice.pk}@mallbyte.invalid>"},
        connection=get_connection(timeout=10),
    )
    if message.send() == 1:
        notice.sent_at = timezone.now()
        notice.save(update_fields=["sent_at"])
