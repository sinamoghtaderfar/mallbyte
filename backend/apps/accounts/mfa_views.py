"""Second-factor endpoints: email OTP -> short-lived challenge -> TOTP/recovery."""

import base64
import hashlib
from datetime import UTC, datetime
from io import BytesIO

import qrcode
from django.contrib.auth.hashers import make_password
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .cookies import set_refresh_cookie
from .mfa import (
    approval_required,
    policy_digest,
    consume_recovery_code,
    decrypt_secret,
    encrypt_secret,
    is_privileged,
    issue_privileged_tokens,
    match_totp,
    new_recovery_codes,
    new_secret,
    provisioning_uri,
)
from .mfa_models import AdminMFAChallenge, AdminMFADevice
from .models import User
from .security_mail import queue_notice
from .serializers import SessionUserSerializer
from .throttles import MFAIPRateThrottle


class ChallengeInput(serializers.Serializer):
    challenge = serializers.CharField(max_length=128)


class SetupInput(ChallengeInput):
    password = serializers.CharField(write_only=True, max_length=1024, trim_whitespace=False)


class VerifyInput(ChallengeInput):
    code = serializers.CharField(max_length=64, write_only=True)


def _get_challenge(raw):
    version, separator, _nonce = raw.partition(".")
    if not separator or not version.isascii() or not version.isdigit():
        return None
    token_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    user_id = AdminMFAChallenge.objects.filter(token_hash=token_hash).values_list("user_id", flat=True).first()
    if not User.objects.select_for_update(no_key=True).filter(pk=user_id).first():
        return None
    challenge = (
        # Lock the related user too: a concurrent security change either wins
        # before this epoch check or revokes the session issued by this request.
        AdminMFAChallenge.objects.select_for_update()
        .select_related("user")
        .filter(token_hash=token_hash)
        .first()
    )
    if (
        challenge is None
        or int(version) != challenge.user.auth_version
        or challenge.used_at is not None
        or challenge.expires_at <= timezone.now()
        or challenge.attempts >= 5
        or not challenge.user.email_verified
        or challenge.policy_digest != policy_digest(challenge.user)
        or not challenge.user.is_active
        or challenge.user.is_deleted
        or not is_privileged(challenge.user)
    ):
        return None
    return challenge


def _invalid():
    return Response({"detail": "Invalid or expired MFA challenge."}, status=status.HTTP_400_BAD_REQUEST)


def _authenticated_response(request, challenge, device, recovery_codes=None):
    user = challenge.user
    from django.conf import settings
    from django.contrib.auth import login

    if user.is_staff and settings.DEBUG and getattr(settings, "DJANGO_ADMIN_ENABLED", True):
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        request.session["verified_admin_device"] = device.session_version
    refresh = issue_privileged_tokens(user, device)
    challenge.session_expires_at = datetime.fromtimestamp(refresh["exp"], UTC)
    challenge.issued_auth_version = user.auth_version
    challenge.issued_device_version = device.session_version
    challenge.save(update_fields=["session_expires_at", "issued_auth_version", "issued_device_version"])
    result = {
        "user": SessionUserSerializer(user).data,
        "access": str(refresh.access_token),
        "is_new": False,
    }
    if recovery_codes is not None:
        result["recovery_codes"] = recovery_codes
    response = Response(result)
    set_refresh_cookie(response, str(refresh))
    return response


class AdminMFASetupView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [MFAIPRateThrottle]

    def post(self, request):
        serializer = SetupInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            challenge = _get_challenge(serializer.validated_data["challenge"])
            if not challenge:
                return _invalid()
            user = challenge.user
            if approval_required(user) and not challenge.approved_by_id:
                return Response({"detail": "An administrator must verify your identity and approve this enrollment."}, status=403)
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
            password = serializer.validated_data["password"]
            if not user.has_usable_password():
                try:
                    validate_password(password, user=user)
                except ValidationError as exc:
                    return Response({"password": exc.messages}, status=400)
                challenge.pending_password = make_password(password)
            elif not user.check_password(password):
                return Response({"detail": "Invalid credentials."}, status=status.HTTP_400_BAD_REQUEST)

            device, _created = AdminMFADevice.objects.select_for_update().get_or_create(
                user=user, defaults={"encrypted_secret": encrypt_secret(new_secret())}
            )
            if device.enabled:
                return Response({"detail": "MFA already enrolled."}, status=status.HTTP_409_CONFLICT)
            secret = new_secret()
            device.encrypted_secret = encrypt_secret(secret)
            device.save(update_fields=["encrypted_secret", "updated_at"])
            AdminMFAChallenge.objects.filter(user=user, used_at=None).exclude(pk=challenge.pk).update(used_at=timezone.now(), pending_password="")
            if not challenge.password_confirmed:
                challenge.attempts = 0
            challenge.password_confirmed = True
            challenge.save(update_fields=["password_confirmed", "pending_password", "attempts"])
        uri = provisioning_uri(user, secret)
        png = BytesIO()
        qrcode.make(uri).save(png, format="PNG")
        return Response({
            "secret": secret,
            "otpauth_uri": uri,
            "qr_png": base64.b64encode(png.getvalue()).decode("ascii"),
            "message": "Add this secret to your authenticator, then confirm its six-digit code.",
        })


class AdminMFACompleteView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [MFAIPRateThrottle]

    def post(self, request):
        serializer = VerifyInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            challenge = _get_challenge(serializer.validated_data["challenge"])
            if not challenge or not challenge.password_confirmed:
                return _invalid()
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
            device = AdminMFADevice.objects.select_for_update().filter(user=challenge.user).first()
            if not device or device.enabled:
                return _invalid()
            counter = match_totp(decrypt_secret(device.encrypted_secret), serializer.validated_data["code"], device.last_counter)
            if counter is None:
                return Response({"detail": "Invalid authenticator code."}, status=status.HTTP_400_BAD_REQUEST)
            recovery, hashes = new_recovery_codes()
            device.last_counter = counter
            device.recovery_codes = hashes
            device.enabled = True
            device.save(update_fields=["last_counter", "recovery_codes", "enabled", "updated_at"])
            challenge.used_at = timezone.now()
            challenge.save(update_fields=["used_at"])
            if challenge.pending_password:
                challenge.user.password = challenge.pending_password
                challenge.user.save(update_fields=["password"])
                challenge.pending_password = ""
                challenge.save(update_fields=["pending_password"])
            queue_notice(challenge.user, "Authenticator enrollment completed.")
            return _authenticated_response(request, challenge, device, recovery_codes=recovery)


class AdminMFAVerifyView(APIView):
    permission_classes = [permissions.AllowAny]
    throttle_classes = [MFAIPRateThrottle]

    def post(self, request):
        serializer = VerifyInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            challenge = _get_challenge(serializer.validated_data["challenge"])
            if not challenge:
                return _invalid()
            challenge.attempts += 1
            challenge.save(update_fields=["attempts"])
            device = AdminMFADevice.objects.select_for_update().filter(user=challenge.user, enabled=True).first()
            if not device:
                return _invalid()

            code = serializer.validated_data["code"].strip()
            counter = match_totp(decrypt_secret(device.encrypted_secret), code, device.last_counter)
            if counter is not None:
                device.last_counter = counter
                device.save(update_fields=["last_counter", "updated_at"])
            elif consume_recovery_code(device, code):
                device.save(update_fields=["recovery_codes", "updated_at"])
            else:
                return Response({"detail": "Invalid authenticator or recovery code."}, status=status.HTTP_400_BAD_REQUEST)

            challenge.used_at = timezone.now()
            challenge.save(update_fields=["used_at"])
            return _authenticated_response(request, challenge, device)
