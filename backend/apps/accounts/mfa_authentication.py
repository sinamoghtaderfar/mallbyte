from django.db import transaction
from django.http import HttpResponseForbidden
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication

from .auth_tokens import identity_token_valid
from .mfa import privileged_token_valid


class BearerTransactionMiddleware:
    """Keep a verified request and any competing privilege change in commit order."""
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        native_admin = path.startswith("/admin/") and path not in ("/admin/login/", "/admin/logout/") and request.user.is_authenticated
        scheme = request.headers.get("Authorization", "").split(None, 1)[:1]
        bearer_api = path.startswith("/api/") and scheme and scheme[0].lower() == "bearer"
        if native_admin or bearer_api:
            with transaction.atomic():
                if native_admin and not self.verified_admin_session(request):
                    return HttpResponseForbidden("Sign in through Mallbyte using email OTP and your authenticator.")
                response = self.get_response(request)
                if response.status_code >= 500:
                    transaction.set_rollback(True)
        else:
            response = self.get_response(request)
        if request.path.startswith(("/api/auth/", "/api/rbac/")):
            response["Cache-Control"] = "private, no-store"
        return response

    @staticmethod
    def verified_admin_session(request):
        from .models import User
        from .mfa_models import AdminMFADevice

        actor = User.objects.select_for_update(no_key=True).filter(
            pk=request.user.pk, is_staff=True, is_active=True, auth_version=request.user.auth_version
        ).first()
        if actor is None:
            return False
        request.user = actor
        return AdminMFADevice.objects.filter(user=actor, enabled=True,
            session_version=request.session.get("verified_admin_device")).exists()


class MFAGuardedJWTAuthentication(JWTAuthentication):
    """Reject legacy/non-MFA JWTs for staff and superusers at every API endpoint."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        if transaction.get_connection().in_atomic_block:
            user = self.user_model._base_manager.select_for_update(no_key=True).filter(pk=user.pk).first()
            if user is None:
                raise AuthenticationFailed("Account unavailable.")
        if not identity_token_valid(user, validated_token):
            raise AuthenticationFailed("Please verify your email and sign in again.", code="session_expired")
        if not privileged_token_valid(user, validated_token):
            raise AuthenticationFailed(
                "A verified MFA session is required for privileged accounts.",
                code="mfa_required",
            )
        return user
