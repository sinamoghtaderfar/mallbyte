"""Add missing artwork to categories recorded in the existing demo manifest."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.analytics.management.commands.seed_marketplace_demo import environment
from apps.analytics.marketplace_demo.catalog import category_artwork, category_image_name
from apps.analytics.marketplace_demo.ownership import Inventory, remove_files
from apps.analytics.models import MarketplaceDemoRun
from apps.products.models import Category


class Command(BaseCommand):
    help = "Fill only missing category images in the recorded development marketplace demo."
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        environment()
        inventory = Inventory()
        dry_run = options["dry_run"]
        report = {"created": 0, "would_create": 0, "skipped": 0, "failed": 0, "failed_category_ids": []}
        try:
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(584230191)")
                run = MarketplaceDemoRun.objects.filter(pk=1).first()
                owned = set(run.rows.get("products.Category", [])) if run else set()
                categories = Category.objects.filter(pk__in=owned).select_related("parent").order_by("pk")
                if not dry_run:
                    categories = categories.select_for_update(of=("self",))
                found = set()
                for category in categories:
                    found.add(category.pk)
                    if category.image:
                        report["skipped"] += 1
                        continue
                    if category.parent_id and category.parent_id not in owned:
                        report["failed_category_ids"].append(category.pk)
                        continue
                    try:
                        image = category_artwork(category)
                        if dry_run:
                            report["would_create"] += 1
                        else:
                            with transaction.atomic():
                                path = inventory.image(category_image_name(category), image)
                                Category.objects.filter(pk=category.pk).update(image=path)
                            report["created"] += 1
                    except (ValueError, OSError):
                        report["failed_category_ids"].append(category.pk)
                report["failed_category_ids"].extend(sorted(owned - found))
                if inventory.files:
                    run.files.extend(inventory.files)
                    run.save(update_fields=["files"])
        except BaseException:
            remove_files(inventory.files)
            raise
        report["failed"] = len(report["failed_category_ids"])
        self.stdout.write(json.dumps(report, sort_keys=True))
        if report["failed"]:
            raise CommandError("Some demo categories could not be backfilled. Review the reported category IDs.")
