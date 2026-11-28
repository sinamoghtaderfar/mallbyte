"""Serialize policy edits with authentication and revoke only affected accounts."""
from django.contrib.auth.models import Group
from django.db.models import F
from django.db.models.signals import m2m_changed, post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver
from django.utils import timezone

from apps.accounts.mfa_models import AdminMFAChallenge
from apps.accounts.models import OTP, User
from apps.accounts.security_mail import queue_notice

from .models import AdminLog, Permission, Role, RolePermission, UserRole
from .privileges import authority


@receiver(pre_delete, sender=User)
def protect_last_admin(sender, instance, origin=None, **kwargs):
    if instance.is_superuser:
        removing = origin.values_list("pk", flat=True) if getattr(origin, "model", None) is User else None
        instance.protect_last_enrolled_admin(removing)


@receiver(pre_delete, sender=Group)
def before_group_delete(sender, instance, **kwargs):
    instance._before_authority = policy_snapshot(instance.custom_user_set.values_list("pk", flat=True))


@receiver(post_delete, sender=Group)
def after_group_delete(sender, instance, **kwargs):
    revoke_changes(instance.__dict__.pop("_before_authority", ()))


def policy_snapshot(user_ids):
    users = User.objects.select_for_update(no_key=True).filter(pk__in=user_ids).order_by("pk")
    return [(user, authority(user)) for user in users]


def revoke_changes(snapshot):
    for user, previous in snapshot:
        current = authority(user)
        if current == previous:
            continue
        # Through-table deletes can emit both model and many-to-many signals.
        updated = User.objects.filter(pk=user.pk, auth_version=user.auth_version).update(auth_version=F("auth_version") + 1)
        if not updated:
            continue
        OTP.objects.filter(email=user.email, is_used=False).update(is_used=True)
        AdminMFAChallenge.objects.filter(user=user, used_at=None).update(used_at=timezone.now(), pending_password="")
        event = "Privileged access granted or changed. A fresh MFA sign-in is required." if current - previous else "Privileged access removed or reduced."
        AdminLog.objects.create(admin=None, action="change_permission", target_user=user,
                                details={"granted": sorted(current - previous), "removed": sorted(previous - current)})
        queue_notice(user, event)


def before_policy_write(sender, instance, raw=False, origin=None, **kwargs):
    if raw or (origin is not None and getattr(origin, "model", type(origin)) != sender):
        return
    if sender in (Role, Permission) and instance._state.adding:
        return
    if sender in (UserRole, RolePermission):
        existing = sender.objects.filter(pk=instance.pk)
        role_ids = [instance.role_id, *existing.values_list("role_id", flat=True)]
    elif sender is Permission:
        role_ids = RolePermission.objects.filter(permission=instance).values_list("role_id", flat=True)
    else:
        role_ids = [instance.pk]
    roles = list(Role.objects.select_for_update().filter(pk__in=role_ids).order_by("pk").values_list("pk", flat=True))
    ids = UserRole.objects.filter(role_id__in=roles).values_list("user_id", flat=True)
    if sender is UserRole:
        ids = [instance.user_id, *existing.values_list("user_id", flat=True)]
    instance._before_authority = policy_snapshot(ids)


def after_policy_write(sender, instance, **kwargs):
    revoke_changes(instance.__dict__.pop("_before_authority", ()))


for policy_model in (Role, Permission, UserRole, RolePermission):
    for signal in (pre_save, pre_delete):
        signal.connect(before_policy_write, sender=policy_model)
    for signal in (post_save, post_delete):
        signal.connect(after_policy_write, sender=policy_model)


def django_grant_change(sender, instance, action, reverse, pk_set, **kwargs):
    if action.startswith("post_"):
        revoke_changes(instance.__dict__.pop("_before_m2m_authority", ()))
        return
    if sender is User.roles.through:
        roles = Role.objects.filter(pk=instance.pk) if reverse else Role.objects.filter(
            pk__in=instance.roles.values_list("pk", flat=True) if pk_set is None else pk_set)
        list(roles.select_for_update().order_by("pk").values_list("pk", flat=True))
    elif sender in (User.groups.through, Group.permissions.through):
        if isinstance(instance, Group):
            groups = Group.objects.filter(pk=instance.pk)
        elif pk_set is not None:
            groups = Group.objects.filter(pk__in=pk_set)
        else:
            groups = instance.groups.all() if isinstance(instance, User) else instance.group_set.all()
        list(groups.select_for_update().order_by("pk").values_list("pk", flat=True))
    if isinstance(instance, User):
        ids = [instance.pk]
    elif sender is Group.permissions.through:
        groups = Group.objects.filter(pk=instance.pk) if not reverse else instance.group_set.all()
        if reverse and pk_set is not None:
            groups = Group.objects.filter(pk__in=pk_set)
        ids = User.objects.filter(groups__in=groups).values_list("pk", flat=True)
    elif pk_set is not None:
        ids = pk_set
    else:
        ids = (instance.users if sender is User.roles.through else instance.custom_user_set).values_list("pk", flat=True)
    instance._before_m2m_authority = policy_snapshot(ids)


for through in (User.groups.through, User.user_permissions.through, Group.permissions.through, User.roles.through):
    m2m_changed.connect(django_grant_change, sender=through)
