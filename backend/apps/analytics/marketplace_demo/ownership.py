"""Explicit ownership, including rows produced by the settlement posting services."""

import uuid
from collections import defaultdict
from io import BytesIO

from django.apps import apps
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.management.base import CommandError
from django.db import connection, transaction
from django.db.models import F, FileField

from apps.accounts.models import Seller, User


class Inventory:
    def __init__(self):
        self.rows = defaultdict(list)
        self.files = []
        self.folder = f"marketplace-demo/{uuid.uuid4().hex}"

    def own(self, obj):
        self.rows[obj._meta.label].append(str(obj.pk) if isinstance(obj.pk, uuid.UUID) else obj.pk)
        return obj

    def create(self, model, at=None, **fields):
        obj = self.own(model.objects.create(**fields))
        if at:
            times = {
                f.name: at
                for f in model._meta.fields
                if f.name in ("created_at", "updated_at", "applied_at")
            }
            if times:
                model.objects.filter(pk=obj.pk).update(**times)
        return obj

    def image(self, filename, image):
        buf = BytesIO()
        image.save(buf, format="PNG")
        path = default_storage.save(f"{self.folder}/{filename}.png", ContentFile(buf.getvalue()))
        self.files.append(path)
        return path


def remove_files(files):
    for path in files:
        default_storage.delete(path)


def cleanup(run, protected):
    # Django's PostgreSQL FKs are deferred NO ACTION. Delete exact manifest
    # keys without Django's cascade/SET_NULL collector touching unrelated rows.
    # Validate *before* committing or scheduling the corresponding file removal.
    owned_users = User._base_manager.select_for_update().filter(
        pk__in=run.rows.get("accounts.User", [])
    )
    for user in owned_users:
        if (
            user.email == protected
            or user.is_superuser
            or user.is_staff
            or user.auth_version != 1
            or user.has_usable_password()
            or user.last_login
            or user.is_deleted
        ):
            raise CommandError("An owned account has been claimed or protected. Cleanup stopped.")
    for change in run.reused_stores:
        profile = Seller.objects.select_for_update().get(pk=change["pk"])
        if (
            profile.user_id != change["user"]
            or profile.user.email == protected
            or profile.user.is_superuser
            or profile.user.is_staff
        ):
            raise CommandError("A reused seller is now protected.")
        fields = {}
        for name, value in change["filled"].items():
            if str(getattr(profile, name)) == value:
                fields[name] = ""
        for name, delta in change["totals"].items():
            fields[name] = F(name) - delta
        Seller.objects.filter(pk=profile.pk).update(**fields)
    with connection.cursor() as cursor:
        for label, ids in run.rows.items():
            model = apps.get_model(label)
            table = connection.ops.quote_name(model._meta.db_table)
            pk = connection.ops.quote_name(model._meta.pk.column)
            cursor.execute(
                f"DELETE FROM {table} WHERE {pk} = ANY(%s)",
                [[model._meta.pk.to_python(key) for key in ids]],
            )
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        cursor.execute("SET CONSTRAINTS ALL DEFERRED")
    # If another record now shares an image, leave its copy in storage.
    files = set(run.files)
    for model in apps.get_models():
        for field in model._meta.local_fields:
            if files and isinstance(field, FileField):
                shared = model._base_manager.filter(**{f"{field.name}__in": files})
                files.difference_update(shared.values_list(field.name, flat=True))
    run.rows = {}
    run.reused_stores = []
    run.files = sorted(files)
    run.save(update_fields=["rows", "reused_stores", "files"])

    def finish():
        remove_files(run.files)
        type(run).objects.filter(
            pk=run.pk, rows={}, fingerprint=run.fingerprint, files=run.files
        ).delete()

    transaction.on_commit(finish)
