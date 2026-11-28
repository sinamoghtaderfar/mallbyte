from django.core.management.base import BaseCommand

from apps.rbac.privileges import privileged_users


class Command(BaseCommand):
    help = "Read-only inventory of active privileged accounts and authenticator readiness."

    def add_arguments(self, parser):
        parser.add_argument("--list-ids", action="store_true", help="Include internal account IDs needing enrollment.")

    def handle(self, *args, **options):
        enrolled = 0
        super_admins = 0
        pending = []
        for user in privileged_users().filter(is_active=True).select_related("admin_mfa").iterator():
            device = getattr(user, "admin_mfa", None)
            if user.email_verified and device and device.enabled:
                enrolled += 1
                super_admins += int(user.is_superuser)
            else:
                pending.append(user.pk)
        self.stdout.write(f"Enrolled: {enrolled}. Enrollment or email verification required: {len(pending)}. Enrolled Super Admins: {super_admins}.")
        if options["list_ids"]:
            self.stdout.write("Account IDs requiring attention: " + ", ".join(map(str, pending)))
