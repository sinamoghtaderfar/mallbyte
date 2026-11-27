import hashlib
import json
import os
import secrets
import tempfile
from datetime import date
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from django.core import mail
from django.core.files.storage import default_storage
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.models import Count, F, Sum
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.mfa_models import AdminMFADevice
from apps.accounts.models import Seller, User
from apps.analytics.models import MarketplaceDemoRun
from apps.discounts.models import Discount
from apps.inventory.models import Stock, Warehouse
from apps.orders.models import Order, OrderItem
from apps.payments.models import Payment
from apps.products.models import Category, Product, ProductImage, Wishlist
from apps.rbac.models import Role, UserRole
from apps.returns.models import ReturnRequest
from apps.reviews.models import ProductReview
from apps.settlements.models import ConfirmedPayout, ConfirmedRefund, ItemAllocation
from apps.settlements.reconciliation import reconciliation_report
from apps.settlements.services import seller_balance
from apps.shipping.models import Shipment
from apps.support.models import SupportTicket

SMALL = {
    "sellers": 2,
    "categories": 3,
    "products": 12,
    "warehouses": 2,
    "customers": 8,
    "orders": 20,
    "tickets": 4,
    "discounts": 2,
    "reviews": 20,
}


@override_settings(DEBUG=True, SETTINGS_MODULE="config.settings.base")
class MarketplaceDemoTests(TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        media = self.settings(MEDIA_ROOT=directory.name, SETTINGS_MODULE="config.settings.base")
        media.enable()
        self.addCleanup(media.disable)
        variables = patch.dict(
            os.environ,
            {
                "MARKETPLACE_DEMO_DATABASE": connection.settings_dict["NAME"],
                "MARKETPLACE_DEMO_ENV": "demo",
                "MARKETPLACE_DEMO_SELLER_PREFIX": "studio+seller",
                "MARKETPLACE_DEMO_SELLER_DOMAIN": "example.invalid",
                "MARKETPLACE_DEMO_SUPERUSER_EMAIL": "operator@example.invalid",
            },
        )
        variables.start()
        self.addCleanup(variables.stop)
        self.media = Path(directory.name)
        self.api = APIClient()

    def command(self, **options):
        output = StringIO()
        call_command("seed_marketplace_demo", stdout=output, **options)
        return json.loads(output.getvalue())

    def test_default_dataset_storefront_ledgers_and_dashboard_endpoints(self):
        admin = User.objects.create_superuser("operator@example.invalid")
        plan = self.command(dry_run=True)
        self.assertFalse(Order.objects.exists())
        self.assertFalse(list(self.media.iterdir()))
        result = self.command()
        self.assertEqual(result["dataset"], plan["dataset"])
        self.assertEqual(self.command()["creates"], 0)
        self.assertEqual(len(mail.outbox), 0)
        counts = (
            (User, 211),
            (Seller, 10),
            (Warehouse, 5),
            (Product, 500),
            (Order, 1000),
            (Payment, 1000),
            (ProductReview, 1500),
            (SupportTicket, 50),
            (Discount, 30),
            (ConfirmedPayout, 10),
            (Category, 60),
            (Stock, 500),
            (ProductImage, 500),
        )
        for model, expected in counts:
            self.assertEqual(model.objects.count(), expected, model.__name__)
        self.assertEqual(ReturnRequest.objects.count(), plan["dataset"]["returns"])
        self.assertEqual(ConfirmedRefund.objects.count(), ReturnRequest.objects.count())
        self.assertEqual(Shipment.objects.count(), plan["dataset"]["shipments"])
        self.assertEqual(OrderItem.objects.count(), ItemAllocation.objects.count())
        self.assertFalse(
            ProductReview.objects.exclude(order_item__order__status="delivered").exists()
        )
        self.assertFalse(
            ProductReview.objects.exclude(customer_id=F("order_item__order__user_id")).exists()
        )
        self.assertFalse(
            ProductReview.objects.exclude(product_id=F("order_item__product_id")).exists()
        )
        self.assertFalse(
            ProductReview.objects.values("product", "customer")
            .annotate(n=Count("pk"))
            .filter(n__gt=1)
            .exists()
        )
        stock = dict(Stock.objects.values_list("product_id", "quantity"))
        from apps.inventory.models import StockMovement

        self.assertEqual(
            stock,
            dict(
                StockMovement.objects.order_by()
                .values("product_id")
                .annotate(total=Sum("quantity"))
                .values_list("product_id", "total")
            ),
        )
        self.assertEqual(reconciliation_report()["total_findings"], 0)
        seen = set()
        for page in range(1, 6):
            response = self.api.get(reverse("product-list"), {"page_size": 100, "page": page})
            self.assertEqual(response.status_code, 200)
            for row in response.data["results"]:
                seen.add(row["id"])
                self.assertTrue(row["main_image"])
                self.assertTrue(row["brand_name"])
                self.assertGreater(row["available_stock"], 0)
        self.assertEqual(len(seen), 500)
        hashes = set()
        for image in ProductImage.objects.all():
            with default_storage.open(image.image.name) as f:
                hashes.add(hashlib.sha256(f.read()).digest())
                f.seek(0)
                Image.open(f).verify()
        self.assertEqual(len(hashes), 500)
        stores = list(Seller.objects.order_by("pk"))
        for i, store in enumerate(stores):
            owned = set(Product.objects.filter(seller=store.user).values_list("pk", flat=True))
            self.assertEqual(len(owned), 50)
            self.assertEqual(
                Order.objects.filter(items__product__seller=store.user).distinct().count(), 100
            )
            self.assertGreater(store.total_sales, 0)
            self.assertEqual(store.total_orders, 100)
            self.assertGreater(
                ItemAllocation.objects.filter(seller=store.user).aggregate(v=Sum("commission"))[
                    "v"
                ],
                0,
            )
            self.assertTrue(ConfirmedPayout.objects.filter(seller=store.user).exists())
            self.assertEqual(store.balance, seller_balance(store.user_id, "IRR"))
            self.api.force_authenticate(store.user)
            params = {"seller_id": stores[(i + 1) % 10].user_id, "period": "month"}
            catalog = self.api.get(reverse("product-my-products"), dict(params, page_size=100))
            self.assertEqual(catalog.status_code, 200)
            self.assertEqual({row["id"] for row in catalog.data["results"]}, owned)
            eligibility = self.api.get(
                reverse("seller-payout-eligibility"), dict(params, currency="IRR")
            )
            self.assertEqual(Decimal(eligibility.data["ledger_balance"]), store.balance)
            summary = self.api.get(reverse("analytics-seller-dashboard"), params)
            self.assertEqual(summary.status_code, 200)
            self.assertEqual(summary.data["scope"]["seller_id"], store.user_id)
            self.assertGreater(Decimal(summary.data["currencies"][0]["net_merchandise_value"]), 0)
            chart = self.api.get(reverse("analytics-seller-timeseries"), params)
            self.assertEqual(chart.status_code, 200)
            self.assertTrue(chart.data["series"][0]["points"])
            top = self.api.get(reverse("analytics-seller-products"), params)
            self.assertEqual(top.status_code, 200)
            rows = top.data["products_by_currency"][0]["products"]
            self.assertTrue(rows)
            self.assertTrue(all(row["product_id"] in owned for row in rows))
            statement = self.api.get(reverse("seller-financial-statement"), params)
            self.assertEqual(statement.status_code, 200)
            self.assertEqual(Decimal(statement.data["currencies"][0]["balance"]), store.balance)
            self.assertEqual(
                self.api.get(reverse("seller_dashboard"), params).data["store_slug"],
                store.store_slug,
            )
            self.assertEqual(self.api.get(reverse("analytics-dashboard")).status_code, 403)
        self.api.force_authenticate(admin)
        dashboard = self.api.get(reverse("analytics-dashboard"), {"period": "all"})
        self.assertEqual(dashboard.status_code, 200)
        for section, key, count in (
            ("orders", "total_orders", 1000),
            ("reviews", "total_reviews", 1500),
            ("support", "total_tickets", 50),
            ("products", "total_products", 500),
        ):
            self.assertEqual(dashboard.data[section][key], count)
        self.assertGreater(Decimal(dashboard.data["sales"]["total_revenue"]), 0)
        self.assertEqual(dashboard.data["content"]["banners"]["published"], 1)
        for endpoint in (
            "analytics-financial",
            "analytics-timeseries",
            "analytics-breakdown",
            "analytics-alerts",
            "settlement-admin-balances",
            "observability-health",
            "observability-stats",
        ):
            response = self.api.get(reverse(endpoint))
            self.assertEqual(response.status_code, 200, endpoint)
            self.assertTrue(response.data)
        runs = self.api.get(reverse("settlement-monitoring-runs")).data
        self.assertEqual(runs["results"][0]["status"], "clean")

    def test_reuse_cleanup_preserves_existing_security_and_files(self):
        admin = User.objects.create_superuser(
            "operator@example.invalid", password=secrets.token_urlsafe()
        )
        device = AdminMFADevice.objects.create(user=admin, encrypted_secret="dummy ciphertext")
        user = User.objects.create_user(
            "studio+seller1@example.invalid", password=secrets.token_urlsafe(), is_seller=True
        )
        role = UserRole.objects.create(user=user, role=Role.objects.get(name="vendor"))
        profile = Seller.objects.create(
            user=user,
            store_name="Existing Studio",
            store_slug="existing",
            status="approved",
            total_sales=1000,
            total_orders=1,
            balance=12,
        )
        original = User._base_manager.order_by("pk").values()
        identities = list(original)
        other = self.media / "unrelated.png"
        other.touch()
        self.assertEqual(
            self.command(**SMALL, dry_run=True)["dataset"]["seller_accounts_reused"], 1
        )
        self.command(**SMALL)
        self.assertEqual(self.command(**SMALL)["creates"], 0)
        with self.assertRaises(CommandError):
            self.command(**SMALL, seed=94)
        self.assertEqual(self.command(cleanup=True, dry_run=True)["action"], "would remove")
        with self.captureOnCommitCallbacks(execute=True):
            self.command(cleanup=True)
        self.assertEqual(self.command(cleanup=True)["action"], "empty")
        self.assertEqual(list(User._base_manager.order_by("pk").values()), identities)
        self.assertTrue(AdminMFADevice.objects.filter(pk=device.pk, user=admin).exists())
        self.assertEqual(list(UserRole.objects.values_list("pk", flat=True)), [role.pk])
        profile.refresh_from_db()
        self.assertEqual(
            (profile.store_name, profile.total_sales, profile.total_orders, profile.balance),
            ("Existing Studio", 1000, 1, 12),
        )
        self.assertFalse(profile.logo)
        self.assertEqual([p for p in self.media.rglob("*") if p.is_file()], [other])

    def test_external_dependency_blocks_cleanup_atomically(self):
        self.command(**SMALL)
        outsider = User.objects.create_user("outside@example.invalid")
        wishlist = Wishlist.objects.create(user=outsider, product=Product.objects.first())
        with self.assertRaises(CommandError):
            self.command(cleanup=True)
        self.assertTrue(Wishlist.objects.filter(pk=wishlist.pk).exists())
        self.assertTrue(MarketplaceDemoRun.objects.exists())
        self.assertEqual(self.command(**SMALL)["creates"], 0)

    def test_conflict_rolls_back_data_and_media(self):
        Category.objects.create(name="Occupied", slug="mbdemo-0-mountain-landscapes")
        with self.assertRaises(CommandError):
            self.command(**SMALL)
        self.assertEqual(Category.objects.count(), 1)
        self.assertFalse(User.objects.exists())
        self.assertFalse(any(p.is_file() for p in self.media.rglob("*")))

    def test_fixed_seed_reproduces_artwork_purchases_and_reviews(self):
        def snapshot():
            return (
                list(
                    Product.objects.order_by("sku").values_list(
                        "name", "sku", "price", "created_at"
                    )
                ),
                list(
                    OrderItem.objects.order_by("order__order_number", "product_sku").values_list(
                        "order__order_number",
                        "order__created_at",
                        "order__user__email",
                        "product_sku",
                        "unit_price",
                    )
                ),
                list(
                    ProductReview.objects.order_by("product__sku", "customer__email").values_list(
                        "product__sku", "customer__email", "rating", "status"
                    )
                ),
                [
                    hashlib.sha256(
                        Path(default_storage.path(image.image.name)).read_bytes()
                    ).hexdigest()
                    for image in ProductImage.objects.order_by("product__sku")
                ],
            )

        options = dict(SMALL, seed=42, as_of=date(2025, 7, 10))
        self.command(**options)
        original = snapshot()
        with self.captureOnCommitCallbacks(execute=True):
            self.command(cleanup=True)
        self.command(**options)
        self.assertEqual(snapshot(), original)

    def test_production_and_identity_guards(self):
        for variables in (
            {"MARKETPLACE_DEMO_ENV": "production"},
            {"MARKETPLACE_DEMO_DATABASE": "wrong"},
            {"MARKETPLACE_DEMO_SUPERUSER_EMAIL": "studio+seller1@example.invalid"},
        ):
            with patch.dict(os.environ, variables), self.assertRaises(CommandError):
                self.command(**SMALL, dry_run=True)
        with self.settings(DEBUG=False), self.assertRaises(CommandError):
            self.command(cleanup=True)
        with (
            self.settings(SETTINGS_MODULE="config.settings.production"),
            self.assertRaises(CommandError),
        ):
            self.command(**SMALL)
        user = User.objects.create_superuser("studio+seller1@example.invalid")
        with self.assertRaises(CommandError):
            self.command(**SMALL)
        self.assertEqual(User.objects.get(), user)
