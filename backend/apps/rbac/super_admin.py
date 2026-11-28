"""Super administrator operations. Privileged actions never trust frontend gates."""
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Subquery
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.mfa import is_privileged, policy_digest
from apps.accounts.mfa_models import AdminMFAChallenge, AdminMFADevice
from apps.accounts.models import OTP, Seller
from apps.accounts.security_mail import queue_notice
from apps.orders.models import Order
from apps.products.models import Product
from apps.support.models import SupportTicket

from .models import AdminLog, Permission, Role, RolePermission
from .permissions import IsSuperAdmin
from .privileges import account_authority, grant_codes, with_privilege_flags
from .serializers import ConsoleRolePermissionSerializer, PermissionSerializer, RoleSerializer
from .utils import clear_role_permissions_cache, log_admin_action

User = get_user_model()


class ConsoleUserSerializer(serializers.ModelSerializer):
    roles = serializers.SerializerMethodField()
    mfa_status = serializers.SerializerMethodField()

    def get_mfa_status(self, obj) -> str:
        if getattr(obj, "admin_mfa", None) and obj.admin_mfa.enabled:
            if obj.is_active and obj.email_verified and obj.session_policy:
                rows = []
                for assignment in obj.user_roles.all():
                    if assignment.is_active and not assignment.is_expired:
                        role = assignment.role
                        rows.append((role.name, None))
                        rows.extend((role.name, link.permission.codename) for link in role.role_permissions.all())
                current = account_authority(obj, grant_codes(rows), obj.review_privileged)
                if policy_digest(obj, current) == obj.session_policy:
                    return "Authenticator enrolled · Privileged session active"
            return "Authenticator enrolled"
        if obj.pending_enrollment:
            return "Enrollment pending"
        if obj.is_staff or obj.is_superuser or obj.business_privileged or obj.review_privileged:
            return "Enrollment required"
        return "Standard account"

    class Meta:
        model = User
        fields = ("id", "email", "full_name", "phone", "is_active", "is_staff",
                  "is_superuser", "is_seller", "email_verified", "date_joined", "roles", "mfa_status")
        read_only_fields = fields

    def get_roles(self, obj):
        return [
            {"id": ur.role_id, "name": ur.role.name}
            for ur in obj.user_roles.all()
            if ur.is_active and not ur.is_expired
        ]


class UserStatusSerializer(serializers.Serializer):
    is_active = serializers.BooleanField()
    reason = serializers.CharField(max_length=500, min_length=5, trim_whitespace=True)


class ApprovalInput(serializers.Serializer):
    reference = serializers.UUIDField()
    identity_verified = serializers.BooleanField()

    def validate_identity_verified(self, value):
        if value is not True:
            raise serializers.ValidationError("Independently verify the person's identity first.")
        return value


class RecoveryInput(ApprovalInput):
    reference = None
    case_reference = serializers.RegexField(r"^[A-Za-z0-9_-]{4,64}$")


class ConsoleUsersViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = ConsoleUserSerializer
    permission_classes = [IsSuperAdmin]

    def get_queryset(self):
        sessions = AdminMFAChallenge.objects.filter(user=OuterRef("pk"), session_expires_at__gt=timezone.now(),
            issued_auth_version=OuterRef("auth_version"), issued_device_version=OuterRef("admin_mfa__session_version"))
        queryset = with_privilege_flags(User.objects.select_related("admin_mfa")).prefetch_related("user_roles__role__role_permissions__permission").annotate(
            session_policy=Subquery(sessions.order_by("-session_expires_at").values("policy_digest")[:1]),
            pending_enrollment=Exists(AdminMFAChallenge.objects.filter(user=OuterRef("pk"), issued_auth_version=OuterRef("auth_version"), used_at=None,
                                       expires_at__gt=timezone.now(), attempts__lt=5)),
        )
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(email__icontains=search) | Q(full_name__icontains=search)
            )
        active = self.request.query_params.get("active")
        if active in ("true", "false"):
            queryset = queryset.filter(is_active=(active == "true"))
        return queryset.order_by("-date_joined")

    @extend_schema(request=ApprovalInput, responses={204: None})
    @action(detail=True, methods=["post"], url_path="approve-enrollment")
    @transaction.atomic
    def approve_enrollment(self, request, pk=None):
        target = self.get_object()
        data = ApprovalInput(data=request.data)
        data.is_valid(raise_exception=True)
        if target.pk == request.user.pk:
            return Response({"detail": "A different administrator must approve enrollment."}, status=403)
        target = User.objects.select_for_update().get(pk=target.pk)
        challenge = AdminMFAChallenge.objects.select_for_update().filter(
            user=target, issued_auth_version=target.auth_version, approval_reference=data.validated_data["reference"], used_at=None,
            expires_at__gt=timezone.now(), policy_digest=policy_digest(target), attempts__lt=5,
        ).first()
        if not (challenge and target.email_verified and target.is_active and is_privileged(target)) or AdminMFADevice.objects.filter(user=target, enabled=True).exists():
            raise ValidationError({"reference": "No eligible pending enrollment for this account."})
        if not challenge.approved_by_id:
            challenge.approved_by = request.user
            challenge.save(update_fields=["approved_by"])
            log_admin_action(request.user, "change_permission", target_user=target,
                             details={"event": "Independent enrollment identity check completed"}, request=request)
        return Response(status=204)

    @extend_schema(request=RecoveryInput, responses={204: None})
    @action(detail=True, methods=["post"], url_path="reset-authenticator")
    @transaction.atomic
    def reset_authenticator(self, request, pk=None):
        target = User.objects.select_for_update().filter(pk=pk, email_verified=True, is_active=True).first()
        data = RecoveryInput(data=request.data)
        data.is_valid(raise_exception=True)
        if not target or target.pk == request.user.pk:
            return Response({"detail": "Recovery requires a different verified account."}, status=403)
        device = AdminMFADevice.objects.select_for_update().filter(user=target, enabled=True).first()
        if not device:
            raise ValidationError({"detail": "No enrolled device."})
        if target.is_superuser and not AdminMFADevice.objects.filter(
            user_id=request.user.pk, user__is_superuser=True, user__is_active=True, enabled=True
        ).exists():
            return Response({"detail": "A second enrolled Super Admin is required."}, status=403)
        device.delete()
        target.operator_provisioned_password = False
        target.save(update_fields=["operator_provisioned_password"])
        target.invalidate_sessions()
        OTP.objects.filter(email=target.email, is_used=False).update(is_used=True)
        AdminMFAChallenge.objects.filter(user=target, used_at=None).update(used_at=timezone.now(), pending_password="")
        log_admin_action(request.user, "change_permission", target_user=target, request=request,
                         details={"event": "Authenticator reset after independent identity verification",
                                  "case": data.validated_data["case_reference"]})
        queue_notice(target, "An administrator reset your authenticator after identity verification.")
        return Response(status=204)

    @action(detail=True, methods=["post"], url_path="set-status")
    def set_status(self, request, pk=None):
        target = self.get_object()
        payload = UserStatusSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        desired = payload.validated_data["is_active"]
        if target.is_superuser or target.is_staff or target.pk == request.user.pk:
            return Response(
                {"detail": "Privileged accounts cannot be changed here."},
                status=status.HTTP_403_FORBIDDEN,
            )
        with transaction.atomic():
            target.is_active = desired
            target.save(update_fields=["is_active"])
            log_admin_action(
                admin=request.user,
                action="activate_user" if desired else "suspend_user",
                target_user=target,
                details={"reason": payload.validated_data["reason"]},
                request=request,
            )
        return Response(self.get_serializer(target).data)


class ConsoleOverviewView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        return Response({
            "users": User.objects.count(),
            "active_users": User.objects.filter(is_active=True).count(),
            "sellers": Seller.objects.count(),
            "pending_sellers": Seller.objects.filter(status=Seller.StatusChoices.PENDING).count(),
            "products": Product.objects.count(),
            "pending_products": Product.objects.filter(status=Product.StatusChoices.PENDING).count(),
            "orders": Order.objects.count(),
            "open_tickets": SupportTicket.objects.exclude(status__in=[
                SupportTicket.StatusChoices.RESOLVED, SupportTicket.StatusChoices.CLOSED
            ]).count(),
            "roles": Role.objects.count(),
            "recent_admin_actions": AdminLog.objects.count(),
        })


class ConsoleProductSerializer(serializers.ModelSerializer):
    seller_email = serializers.EmailField(source="seller.email", read_only=True)

    class Meta:
        model = Product
        fields = ("id", "name", "sku", "status", "is_active", "is_featured", "seller_email", "created_at")
        read_only_fields = fields


class ConsoleProductsViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsSuperAdmin]
    serializer_class = ConsoleProductSerializer

    def get_queryset(self):
        queryset = Product.objects.select_related("seller")
        requested = self.request.query_params.get("status", "")
        if requested in dict(Product.StatusChoices.choices):
            queryset = queryset.filter(status=requested)
        search = self.request.query_params.get("search", "").strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(sku__icontains=search))
        return queryset.order_by("-created_at")


class ConsoleRolesView(APIView):
    permission_classes = [IsSuperAdmin]

    def get(self, request):
        return Response({
            "roles": RoleSerializer(Role.objects.prefetch_related("role_permissions__permission"), many=True).data,
            "permissions": PermissionSerializer(Permission.objects.all(), many=True).data,
        })


class ConsoleRolePermissionView(APIView):
    permission_classes = [IsSuperAdmin]

    def post(self, request, pk):
        role = Role.objects.filter(pk=pk).first()
        if not role:
            return Response({"detail": "Role not found."}, status=404)
        if role.is_system_role:
            return Response({"detail": "System role permissions are protected."}, status=403)
        payload = ConsoleRolePermissionSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        permission = Permission.objects.filter(pk=payload.validated_data["permission_id"]).first()
        if not permission:
            raise ValidationError({"permission_id": "Invalid permission."})
        enabled = payload.validated_data["enabled"]
        with transaction.atomic():
            if enabled:
                RolePermission.objects.get_or_create(role=role, permission=permission)
            else:
                RolePermission.objects.filter(role=role, permission=permission).delete()
            clear_role_permissions_cache(role)
            log_admin_action(
                admin=request.user, action="change_permission", target_role=role,
                details={"permission": permission.codename, "enabled": enabled}, request=request,
            )
        return Response(RoleSerializer(role).data)
