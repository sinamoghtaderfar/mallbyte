from datetime import timedelta
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from apps.content.models import Banner
from apps.products.models import Category, Product
from apps.rbac.models import Role
from apps.rbac.utils import assign_role


def image(ext="png"):
    data = BytesIO()
    Image.new("RGB", (22, 16)).save(data, format={"png": "PNG", "jpg": "JPEG", "webp": "WEBP"}[ext])
    return SimpleUploadedFile(f"art.{ext}", data.getvalue())


class StorefrontBannerTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        users = get_user_model().objects
        self.admin = users.create_superuser(email="cms-admin@example.invalid")
        self.category = Category.objects.create(name="Original editions")
        self.other = Category.objects.create(name="Gifts")
        self.product = Product.objects.create(
            seller=self.admin, category=self.category, name="Print", sku="CMS-TEST-1", price=100,
            status=Product.StatusChoices.APPROVED, qr_code="existing.png",
        )
        self.url = reverse("content-banner-storefront")

    def banner(self, placement, title="Print banner", **fields):
        return Banner.objects.create(
            placement=placement, title=title, image="banners/art.png",
            **{"status": "published", **fields},
        )

    def titles(self, placement, **params):
        response = self.api.get(self.url, {"placement": placement, **params})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        return [row["title"] for row in response.data]

    def test_all_placements_and_order_even_for_authenticated_editors(self):
        now = timezone.now()
        self.api.force_authenticate(self.admin)
        for placement in Banner.PlacementChoices.values:
            self.banner(placement, "Second", order=20)
            self.banner(placement, "First", order=10)
            for fields in [
                {"status": "draft"}, {"status": "archived"},
                {"starts_at": now + timedelta(hours=1)},
                {"ends_at": now - timedelta(hours=1)},
            ]:
                self.banner(placement, "Hidden", order=0, **fields)
            self.assertEqual(
                self.titles(placement, category=self.category.pk, product=self.product.pk),
                ["First", "Second"],
            )
        self.assertEqual(self.titles("category_top"), [])
        self.assertEqual(self.titles("product_detail", product=900000), [])
        home = self.api.get(reverse("content-homepage")).data["banners"]
        self.assertEqual({row["placement"] for row in home}, {"home_hero", "home_top", "home_middle", "global"})
        self.assertEqual(len(home), 8)

    def test_target_precedence_and_general_fallback(self):
        self.banner("category_top", "General category")
        self.banner("category_top", "Target category", category=self.category)
        self.assertEqual(self.titles("category_top", category=self.category.pk), ["Target category"])
        self.assertEqual(self.titles("category_top", category=self.other.pk), ["General category"])
        self.banner("product_detail", "General product")
        self.assertEqual(self.titles("product_detail", product=self.product.pk), ["General product"])
        self.banner("product_detail", "Matching category", category=self.category)
        self.assertEqual(self.titles("product_detail", product=self.product.pk), ["Matching category"])
        specific = self.banner("product_detail", "Matching product", product=self.product)
        self.assertEqual(self.titles("product_detail", product=self.product.pk), ["Matching product"])
        specific.status = "archived"
        specific.save()
        self.assertEqual(self.titles("product_detail", product=self.product.pk), ["Matching category"])

    def test_multipart_target_save_and_validation(self):
        with TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media):
            self.api.force_authenticate(self.admin)
            created = self.api.post(reverse("content-banner-list"), {
                "title": "Targeted", "image": image(), "placement": "product_detail",
                "category": "", "product": self.product.pk, "link_url": "/products",
            }, format="multipart")
            self.assertEqual(created.status_code, 201, created.data)
            self.assertEqual(created.data["product"], self.product.pk)
            detail = reverse("content-banner-detail", args=[created.data["id"]])
            invalid = self.api.patch(detail, {"placement": "checkout"}, format="json")
            self.assertEqual(invalid.status_code, 400)
            invalid = self.api.patch(detail, {"link_url": "javascript:alert(1)"}, format="json")
            self.assertEqual(invalid.status_code, 400)
            published = self.api.post(reverse("content-banner-publish", args=[created.data["id"]]))
            self.assertEqual(published.status_code, 200, published.data)
            self.assertEqual(self.titles("product_detail", product=self.product.pk), ["Targeted"])


class CategoryMediaTests(TestCase):
    def test_upload_replace_remove_and_clear_cache_without_losing_other_categories(self):
        api = APIClient()
        user = get_user_model().objects.create_superuser(email="catalog-admin@example.invalid")
        operator = get_user_model().objects.create_user(email="product-operator@example.invalid")
        role, _ = Role.objects.get_or_create(name="product_admin", defaults={"level": 70})
        assign_role(operator, role, user)
        inactive = Category.objects.create(name="Inactive", is_active=False)
        with TemporaryDirectory() as media, self.settings(MEDIA_ROOT=media, DEBUG=True):
            api.force_authenticate(user)
            created = api.post(reverse("category-list"), {"name": "Postcards", "image": image()}, format="multipart")
            self.assertEqual(created.status_code, 201, created.data)
            pk = created.data["id"]
            detail = reverse("category-detail", args=[pk])
            first = created.data["image"]
            api.force_authenticate(operator)
            for ext in ("jpg", "webp"):
                replaced = api.patch(detail, {"image": image(ext)}, format="multipart")
                self.assertEqual(replaced.status_code, 200, replaced.data)
                self.assertTrue(replaced.data["image"].endswith(f".{ext}"))
                self.assertNotEqual(replaced.data["image"], first)
                public = APIClient().get(detail)
                self.assertEqual(public.data["image"], replaced.data["image"])
                self.assertEqual(public["Cache-Control"], "private, no-store")
            disguised = image()
            disguised.name = "disguised.jpg"
            for invalid in (disguised, SimpleUploadedFile("fake.png", b"not an image")):
                self.assertEqual(api.patch(detail, {"image": invalid}, format="multipart").status_code, 400)
            self.assertEqual(api.patch(detail, {"image": ""}, format="multipart").data["image"], None)
            self.assertEqual(api.get(reverse("category-manage")).data["count"], 2)
            api.force_authenticate(get_user_model().objects.create_user(email="shopper@example.invalid"))
            self.assertEqual(api.patch(detail, {"image": image()}, format="multipart").status_code, 403)
            self.assertEqual(api.get(reverse("category-manage")).status_code, 403)
            self.assertEqual(api.get(reverse("category-detail", args=[inactive.pk])).status_code, 404)
