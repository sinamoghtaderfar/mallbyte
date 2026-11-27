from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.inventory.models import Stock, Warehouse
from apps.products.models import Brand, Category, Product


class CategoryFilteringTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.seller = get_user_model().objects.create_user(email="catalog-filter@example.invalid", is_seller=True)
        cls.root = Category.objects.create(name="Root")
        cls.branch = Category.objects.create(name="Branch", parent=cls.root)
        cls.deep = Category.objects.create(name="Deep", parent=cls.branch)
        cls.leaf = Category.objects.create(name="Leaf", parent=cls.deep)
        cls.sibling = Category.objects.create(name="Sibling", parent=cls.root)
        cls.other = Category.objects.create(name="Unrelated")
        cls.empty = Category.objects.create(name="Empty")
        cls.inactive = Category.objects.create(name="Inactive", parent=cls.root, is_active=False)
        below_inactive = Category.objects.create(name="Behind inactive", parent=cls.inactive)
        cls.brand = Brand.objects.create(name="Independent Brand")
        cls.root_product = cls.product(cls.root, "Root Print", 100)
        cls.branch_product = cls.product(cls.branch, "Branch Print", 200, brand=cls.brand)
        cls.leaf_product = cls.product(cls.leaf, "Leaf Print", 300, brand=cls.brand)
        cls.sibling_product = cls.product(cls.sibling, "Sibling Print", 250)
        cls.other_product = cls.product(cls.other, "Elsewhere Print", 200, brand=cls.brand)
        cls.product(cls.branch, "Draft Print", 200, status=Product.StatusChoices.PENDING)
        cls.product(cls.branch, "Disabled Print", 200, is_active=False)
        cls.product(cls.inactive, "Inactive category Print", 200)
        cls.product(below_inactive, "Hidden branch Print", 200)
        warehouse = Warehouse.objects.create(name="Shelf", code="CATEGORY-FILTER")
        Stock.objects.create(product=cls.leaf_product, warehouse=warehouse, quantity=3)

    @classmethod
    def product(cls, category, name, price, **overrides):
        return Product.objects.create(
            category=category, name=name, sku=name, price=price, seller=cls.seller,
            qr_code="products/qr-fixture.png", **{"status": Product.StatusChoices.APPROVED, **overrides},
        )

    def results(self, categories, **params):
        response = self.client.get(reverse("product-list"), {"category": categories, **params})
        self.assertEqual(response.status_code, 200, response.data)
        return response.data, [row["id"] for row in response.data["results"]]

    def test_parent_contains_direct_products_and_arbitrary_depth_descendants(self):
        expected = {p.pk for p in (self.root_product, self.branch_product, self.sibling_product, self.leaf_product)}
        for user in (None, self.seller):
            self.client.force_authenticate(user)
            response, ids = self.results(self.root.pk)
            self.assertEqual(response["count"], 4)
            self.assertEqual(set(ids), expected)

    def test_subcategory_does_not_include_parent_or_siblings(self):
        self.assertCountEqual(self.results(self.branch.pk)[1], [self.branch_product.pk, self.leaf_product.pk])
        self.assertEqual(self.results(self.leaf.pk)[1], [self.leaf_product.pk])

    def test_multiple_and_overlapping_roots_return_unique_paginated_results(self):
        selected = [self.root.pk, self.branch.pk, self.other.pk, self.root.pk]
        first, first_ids = self.results(selected, ordering="price", page_size=3)
        second, second_ids = self.results(selected, ordering="price", page_size=3, page=2)
        self.assertEqual((first["count"], second["count"]), (5, 5))
        self.assertEqual(first_ids[0], self.root_product.pk)
        self.assertEqual(second_ids, [self.sibling_product.pk, self.leaf_product.pk])
        self.assertEqual(len(set(first_ids + second_ids)), 5)

    def test_search_brand_price_and_availability_work_with_category(self):
        args = {"search": "Print", "brand": self.brand.pk, "min_price": 190, "max_price": 300, "ordering": "-price"}
        self.assertEqual(self.results(self.root.pk, **args)[1], [self.leaf_product.pk, self.branch_product.pk])
        self.assertEqual(self.results(self.root.pk, in_stock="true", **args)[1], [self.leaf_product.pk])
        self.assertEqual(self.results(self.root.pk, in_stock="false", **args)[1], [self.branch_product.pk])

    def test_missing_category_preserves_unfiltered_product_listing_and_retrieval(self):
        url = reverse("product-list")
        all_products = self.client.get(url)
        self.assertEqual(all_products.status_code, 200)
        ids = {row["id"] for row in all_products.data["results"]}
        self.assertTrue({self.root_product.pk, self.branch_product.pk, self.other_product.pk} <= ids)
        self.assertEqual(self.client.get(reverse("product-detail", args=[self.leaf_product.pk])).status_code, 200)

    def test_empty_inactive_and_invalid_selections(self):
        for category in [self.empty, self.inactive]:
            self.assertEqual(self.results(category.pk)[0]["count"], 0)
        for value in ["broken", "1.5", "-1", str(2 ** 63), self.leaf.pk + 100_000]:
            with self.subTest(value=value):
                response = self.client.get(reverse("product-list"), {"category": value})
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn("category", response.data)

    def test_cycles_terminate_and_queries_do_not_grow_with_depth(self):
        with CaptureQueriesContext(connection) as narrow:
            self.results(self.leaf.pk)
        Category.objects.filter(pk=self.root.pk).update(parent=self.leaf)
        with CaptureQueriesContext(connection) as wide:
            response, ids = self.results([self.root.pk, self.branch.pk])
        self.assertEqual(response["count"], 4)
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(len(narrow), len(wide))
        self.assertLessEqual(len(wide), 6)
