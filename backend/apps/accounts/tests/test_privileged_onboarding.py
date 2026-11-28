"""Security contracts across the public auth and Super Admin interfaces."""
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import timedelta
from threading import Event
from unittest import skipUnless
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.db import close_old_connections, connection, connections, transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient, APITestCase

from apps.accounts.auth_tokens import issue_user_tokens
from apps.accounts.mfa import _totp, encrypt_secret, issue_privileged_tokens, new_secret
from apps.accounts.mfa_models import AdminMFADevice, SecurityNotice
from apps.accounts.models import OTP, User
from apps.accounts.security_mail import deliver, queue_notice
from apps.rbac.models import Permission, Role, RolePermission, UserRole


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
                   MFA_FERNET_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
class PrivilegedOnboardingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.operator = User.objects.create_superuser(email="operator@example.invalid", email_verified=True)
        device = AdminMFADevice.objects.create(user=self.operator, enabled=True, encrypted_secret=encrypt_secret(new_secret()))
        self.admin = APIClient()
        self.admin.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_privileged_tokens(self.operator, device).access_token}")
        self.user = User.objects.create_user(email="customer@example.invalid", email_verified=True)
        self.role = Role.objects.create(name="delegated-editor")
        self.edit, _ = Permission.objects.get_or_create(codename="manage_content", defaults={"name": "Edit", "module": "content"})
        RolePermission.objects.create(role=self.role, permission=self.edit)

    def first_factor(self):
        otp = OTP.generate_otp(self.user.email)
        response = self.client.post("/api/auth/otp/verify/", {"email": self.user.email, "code": otp.code})
        self.assertEqual(response.status_code, 200)
        return response

    def assign(self):
        response = self.admin.post("/api/rbac/assign-role/", {"user_id": self.user.pk, "role_id": self.role.pk})
        self.assertEqual(response.status_code, 201, response.data)

    def approve(self, first):
        url = f"/api/rbac/console/users/{self.user.pk}/approve-enrollment/"
        response = self.admin.post(url, {"reference": first["approval_reference"], "identity_verified": True}, format="json")
        self.assertEqual(response.status_code, 204, response.data)

    def test_promotion_enrollment_and_removal(self):
        customer = self.first_factor()
        old_client = APIClient()
        old_client.credentials(HTTP_AUTHORIZATION=f"Bearer {customer.data['access']}")
        self.assertEqual(old_client.get("/api/rbac/console/overview/").status_code, 403)
        self.assign()
        self.assertEqual(old_client.get("/api/auth/profile/").status_code, 401)
        old_client.credentials()
        old_client.cookies = customer.cookies
        self.assertEqual(old_client.post("/api/auth/token/refresh/").status_code, 401)
        first = self.first_factor().data
        self.assertTrue(first["establish_password"])
        self.assertNotIn("access", first)
        password = "Fresh-verified-enrollment-9437!"
        setup_input = {"challenge": first["challenge"], "password": password}
        self.assertEqual(self.client.post("/api/auth/mfa/setup/", setup_input).status_code, 403)
        self.approve(first)
        self.assertEqual(self.client.post("/api/auth/mfa/setup/", {**setup_input, "password": "123"}).status_code, 400)
        setup = self.client.post("/api/auth/mfa/setup/", setup_input)
        self.assertEqual(setup.status_code, 200, setup.data.keys())
        self.assertTrue(setup.data["qr_png"].startswith("iVBOR"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())
        completed = self.client.post("/api/auth/mfa/complete/", {
            "challenge": first["challenge"], "code": _totp(setup.data["secret"], int(time.time()) // 30)})
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(len(completed.data["recovery_codes"]), 10)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(password))
        self.assertFalse(self.user.is_staff or self.user.is_superuser)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {completed.data['access']}")
        self.assertEqual(self.client.get("/api/rbac/my-permissions/").data["permissions"], ["manage_content"])
        self.assertEqual(self.client.get("/api/rbac/console/overview/").status_code, 403)
        status_url = f"/api/rbac/console/users/{self.user.pk}/"
        self.assertIn("session active", self.admin.get(status_url).data["mfa_status"])
        revoked = self.admin.delete(f"/api/rbac/remove-role/{self.user.pk}/{self.role.pk}/")
        self.assertEqual(revoked.status_code, 200)
        self.assertNotIn("session active", self.admin.get(status_url).data["mfa_status"])
        self.assertEqual(self.client.get("/api/auth/profile/").status_code, 401)
        self.assertTrue(AdminMFADevice.objects.filter(user=self.user, enabled=True).exists())
        self.assign()
        self.client.credentials()
        self.assertTrue(self.first_factor().data["mfa_enrolled"])
        self.assertEqual(self.client.post("/api/auth/token/", {"email": self.user.email, "password": password}).status_code, 403)

    def test_first_privileged_password_requires_approved_enrollment(self):
        self.assign()
        otp = OTP.generate_otp(self.user.email)
        response = self.client.post("/api/auth/password-reset/verify/", {
            "email": self.user.email, "code": otp.code, "new_password": "Mailbox-cannot-establish-5126!"})
        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertFalse(self.user.has_usable_password())

    def test_approval_is_bound_to_one_attempt_and_expires(self):
        self.assign()
        approved = self.first_factor().data
        self.approve(approved)
        other = self.first_factor().data
        self.assertNotEqual(approved["approval_reference"], other["approval_reference"])
        response = self.client.post("/api/auth/mfa/setup/", {"challenge": other["challenge"], "password": "A-strong-password!"})
        self.assertEqual(response.status_code, 403)
        self.user.mfa_challenges.update(expires_at=timezone.now()-timedelta(seconds=1))
        response = self.client.post("/api/auth/mfa/setup/", {"challenge": approved["challenge"], "password": "A-strong-password!"})
        self.assertEqual(response.status_code, 400)

    def test_native_admin_session_must_match_verified_device(self):
        from django.http import HttpResponse
        from django.test import RequestFactory
        from apps.accounts.mfa_authentication import BearerTransactionMiddleware

        request = RequestFactory().get("/admin/")
        request.user = self.operator
        request.session = {}
        middleware = BearerTransactionMiddleware(lambda _: HttpResponse())
        self.assertEqual(middleware(request).status_code, 403)
        request.session["verified_admin_device"] = self.operator.admin_mfa.session_version
        self.assertEqual(middleware(request).status_code, 200)
        self.operator.invalidate_sessions()
        self.assertEqual(middleware(request).status_code, 403)

    def test_last_enrolled_super_admin_cannot_be_disabled_or_deleted(self):
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            self.operator.is_active = False
            self.operator.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied), transaction.atomic():
            User.objects.filter(pk=self.operator.pk).delete()
        self.operator.refresh_from_db()
        self.assertTrue(self.operator.is_active)

    def test_existing_password_on_promotion_still_requires_approval_and_password_confirmation(self):
        self.user.set_password("Established-before-promotion-4918!")
        self.user.save(update_fields=["password"])
        self.assign()
        first = self.first_factor().data
        self.assertFalse(first["establish_password"])
        payload = {"challenge": first["challenge"], "password": "Established-before-promotion-4918!"}
        self.assertEqual(self.client.post("/api/auth/mfa/setup/", payload).status_code, 403)
        self.approve(first)
        self.assertEqual(self.client.post("/api/auth/mfa/setup/", {**payload, "password": "incorrect"}).status_code, 400)
        self.assertEqual(self.client.post("/api/auth/mfa/setup/", payload).status_code, 200)

    def test_django_review_group_moderator_requires_mfa_without_staff_access(self):
        from django.contrib.auth.models import Group, Permission as DjangoPermission
        group = Group.objects.create(name="Delegated review team")
        review = DjangoPermission.objects.get(content_type__app_label="reviews", codename="change_productreview")
        group.permissions.add(review)
        session = self.first_factor().data
        self.user.groups.add(group)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {session['access']}")
        self.assertEqual(self.client.get("/api/auth/profile/").status_code, 401)
        self.client.credentials()
        self.assertTrue(self.first_factor().data["mfa_required"])
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_staff)
        self.user.groups.remove(group)
        self.assertIn("access", self.first_factor().data)

    def test_unverified_target_and_mixed_bulk_rejected_atomically(self):
        other = User.objects.create_user(email="unverified@example.invalid")
        response = self.admin.post("/api/rbac/bulk-assign/", {"user_ids": [self.user.pk, other.pk], "role_ids": [self.role.pk]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserRole.objects.filter(role=self.role).exists())
        self.assertEqual(self.admin.post("/api/rbac/assign-role/", {"user_id": other.pk, "role_id": self.role.pk}).status_code, 400)

    def test_custom_changes_only_revoke_on_effective_change(self):
        self.assign()
        self.user.refresh_from_db()
        epoch = self.user.auth_version
        other = Role.objects.create(name="second-editor")
        RolePermission.objects.create(role=other, permission=self.edit)
        UserRole.objects.create(user=self.user, role=other)
        self.user.refresh_from_db()
        self.assertEqual(epoch, self.user.auth_version)
        pending = self.first_factor().data
        publish, _ = Permission.objects.get_or_create(codename="publish_content", defaults={"name": "Publish", "module": "content"})
        RolePermission.objects.create(role=other, permission=publish)
        self.user.refresh_from_db()
        self.assertGreater(self.user.auth_version, epoch)
        self.assertEqual(self.client.post("/api/auth/mfa/setup/", {"challenge": pending["challenge"], "password": "secure-password"}).status_code, 400)
        UserRole.objects.get(user=self.user, role=other).delete()
        self.assertTrue(UserRole.objects.filter(user=self.user, role=self.role).exists())

    def test_seller_self_service_and_expired_roles(self):
        seller_role, _ = Role.objects.get_or_create(name="vendor")
        own, _ = Permission.objects.get_or_create(codename="edit_own_product", defaults={"name": "Edit own product", "module": "products"})
        RolePermission.objects.get_or_create(role=seller_role, permission=own)
        UserRole.objects.create(user=self.user, role=seller_role)
        UserRole.objects.create(user=self.user, role=self.role, expires_at=timezone.now()-timedelta(seconds=1))
        response = self.first_factor()
        self.assertIn("access", response.data)
        self.assertNotIn("mfa_required", response.data)

    def test_reset_requires_an_independent_super_admin_and_revokes_codes(self):
        self.assign()
        user_device = AdminMFADevice.objects.create(user=self.user, enabled=True, encrypted_secret=encrypt_secret(new_secret()))
        token = issue_privileged_tokens(User.objects.get(pk=self.user.pk), user_device)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token.access_token}")
        payload = {"identity_verified": True, "case_reference": "case-123"}
        url = f"/api/rbac/console/users/{self.user.pk}/reset-authenticator/"
        self.assertEqual(self.client.post(url, payload, format="json").status_code, 403)
        self.assertEqual(self.admin.post(f"/api/rbac/console/users/{self.operator.pk}/reset-authenticator/", payload, format="json").status_code, 403)
        self.assertEqual(self.admin.post(url, payload, format="json").status_code, 204)
        self.assertEqual(self.client.get("/api/auth/profile/").status_code, 401)
        self.client.credentials()
        self.assertIsNotNone(self.first_factor().data["approval_reference"])

    def test_notices_are_commit_bound_retryable_and_deduplicated(self):
        with self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(RuntimeError), transaction.atomic():
                queue_notice(self.user, "Rolled back")
                raise RuntimeError()
        self.assertFalse(SecurityNotice.objects.exists())
        with patch("apps.accounts.security_mail.EmailMessage.send", side_effect=OSError), self.captureOnCommitCallbacks(execute=True):
            queue_notice(self.user, "Access changed")
        notice = SecurityNotice.objects.get()
        self.assertIsNone(notice.sent_at)
        deliver(notice.pk)
        deliver(notice.pk)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("Access changed", mail.outbox[0].body)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
@override_settings(MFA_FERNET_KEY="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class ConcurrentPrivilegeChangeTests(TransactionTestCase):
    def assert_change_precedes_request(self, *, removal):
        user = User.objects.create_user(email="concurrency@example.invalid", email_verified=True)
        role = Role.objects.create(name="concurrency-editor")
        permission, _ = Permission.objects.get_or_create(codename="manage_content", defaults={"module": "content", "name": "Edit"})
        RolePermission.objects.create(role=role, permission=permission)
        if removal:
            UserRole.objects.create(user=user, role=role)
            user.refresh_from_db()
            device = AdminMFADevice.objects.create(user=user, encrypted_secret=encrypt_secret(new_secret()), enabled=True)
            token = issue_privileged_tokens(user, device)
        else:
            token = issue_user_tokens(user)
        bearer = str(token.access_token)
        ready = Event()

        def read_profile():
            close_old_connections()
            try:
                client = APIClient()
                client.credentials(HTTP_AUTHORIZATION=f"Bearer {bearer}")
                ready.set()
                return client.get("/api/auth/profile/").status_code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                if removal:
                    UserRole.objects.filter(user=user, role=role).delete()
                else:
                    UserRole.objects.create(user=user, role=role)
                result = pool.submit(read_profile)
                self.assertTrue(ready.wait(5))
                with self.assertRaises(TimeoutError):
                    result.result(timeout=.25)
            self.assertEqual(result.result(timeout=5), 401)

    def test_request_waits_for_committing_promotion(self):
        self.assert_change_precedes_request(removal=False)

    def test_request_waits_for_committing_removal(self):
        self.assert_change_precedes_request(removal=True)
