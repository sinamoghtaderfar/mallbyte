"""Storage for support evidence, intentionally outside public MEDIA_ROOT."""

from pathlib import Path

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import FileSystemStorage
from django.utils.deconstruct import deconstructible


@deconstructible
class SupportAttachmentStorage(FileSystemStorage):
    """Support files have no public URL; only the authorized API may serve them."""

    def __init__(self):
        super().__init__(location=None, base_url=None)

    @property
    def base_location(self):
        return settings.SUPPORT_PRIVATE_MEDIA_ROOT

    @property
    def location(self):
        private_root = Path(self.base_location).resolve()
        public_root = Path(settings.MEDIA_ROOT).resolve()
        if private_root.is_relative_to(public_root):
            raise ImproperlyConfigured(
                "SUPPORT_PRIVATE_MEDIA_ROOT must be outside the publicly served MEDIA_ROOT."
            )
        return str(private_root)

    def url(self, name):
        raise ValueError("Support attachments have no public URL.")


@deconstructible
class PrivateMediaStorage(SupportAttachmentStorage):
    """Private reports and return evidence share the persisted private volume.

    Reuse the existing private-media root by default so upgrades do not silently
    put worker-generated reports on an unmounted container filesystem.
    """

    @property
    def base_location(self):
        return getattr(settings, "PRIVATE_MEDIA_ROOT", settings.SUPPORT_PRIVATE_MEDIA_ROOT)

    def url(self, name):
        raise ValueError("Private media is available through authorized downloads only.")
