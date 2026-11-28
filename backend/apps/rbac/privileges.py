"""Effective platform authority, independent of staff flags and role levels."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission as DjangoPermission
from django.core.exceptions import ValidationError
from django.db.models import Exists, OuterRef, Q
from django.utils import timezone

from .models import Permission, UserRole

# Each of these is scoped to the seller's own records in the business API.
SELF_SERVICE = frozenset({
    "add_product", "edit_own_product", "delete_own_product",
    "view_own_orders", "view_seller_analytics",
})
# These three historical roles also have explicit role-based API checks.
LEGACY_ROLES = frozenset({"product_admin", "content_admin", "vendor_manager"})


def active_grants(user_id):
    return UserRole.objects.filter(user_id=user_id, is_active=True).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
    )


def grant_codes(rows):
    authority = set()
    for role, code in rows:
        if role in LEGACY_ROLES:
            authority.add("role:" + role)
        if code and code not in SELF_SERVICE:
            authority.add(code)
    return authority


def business_authority(user_id):
    return grant_codes(active_grants(user_id).order_by().values_list("role__name", "role__role_permissions__permission__codename"))


def review_grants(user_id):
    return DjangoPermission.objects.filter(
        Q(custom_user_permissions=user_id) | Q(group__custom_user=user_id),
        content_type__app_label="reviews", codename="change_productreview",
    )


def with_privilege_flags(queryset):
    business = active_grants(OuterRef("pk")).filter(
        Q(role__name__in=LEGACY_ROLES) |
        Q(role__role_permissions__permission__in=Permission.objects.exclude(codename__in=SELF_SERVICE))
    )
    return queryset.annotate(business_privileged=Exists(business), review_privileged=Exists(review_grants(OuterRef("pk"))))


def privileged_users():
    return with_privilege_flags(get_user_model().objects.all()).filter(
        Q(is_staff=True) | Q(is_superuser=True) | Q(business_privileged=True) | Q(review_privileged=True)
    )


def authority(user):
    return account_authority(user, business_authority(user.pk), review_grants(user.pk).exists())


def account_authority(user, result, reviews):
    if reviews:
        result.add("moderate_reviews")
    if user.is_superuser:
        result.add("superuser")
    if user.is_staff:
        result.add("staff")
    return result


def role_requires_mfa(role):
    return role.name in LEGACY_ROLES or role.role_permissions.exclude(permission__codename__in=SELF_SERVICE).exists()


def validate_assignment(user, role):
    if role_requires_mfa(role) and not (user.email_verified and user.is_active and not user.is_deleted):
        raise ValidationError("Privileged roles require an active user with verified email.")
