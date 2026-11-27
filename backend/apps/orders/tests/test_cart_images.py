from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.inventory.models import Stock, Warehouse
from apps.orders.models import Cart, CartItem, Order, OrderItem
from apps.products.models import Category, Product, ProductImage


class CartImageTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.owner = users.create_user(email="image-cart-owner@example.invalid")
        cls.other = users.create_user(email="image-cart-unrelated@example.invalid")
        cls.category = Category.objects.create(name="Example prints")
        cls.warehouse = Warehouse.objects.create(name="Print warehouse", code="PRINTS")
        cls.cart = Cart.objects.create(user=cls.owner)
        cls.print = cls.product("Alpine")
        cls.line = CartItem.objects.create(cart=cls.cart, product=cls.print, quantity=1, unit_price=200)
        cls.add_image(cls.print, "gallery-first", order=1)
        cls.add_image(cls.print, "main", is_main=True, order=5)
        cls.add_image(cls.print, "gallery-second", order=1)
        cls.add_image(cls.print, "later", order=8)
        ProductImage.objects.create(product=cls.print, image="", order=0)

    @classmethod
    def product(cls, name):
        product = Product.objects.create(
            seller=cls.other, category=cls.category, name=name, sku=f"CART-IMG-{name}", price=200,
            status=Product.StatusChoices.APPROVED, qr_code="products/qr-fixture.png",
        )
        Stock.objects.create(product=product, warehouse=cls.warehouse, quantity=15)
        return product

    @staticmethod
    def add_image(product, label, **kwargs):
        return ProductImage.objects.create(product=product, image=f"products/{label}.png", **kwargs)

    def setUp(self):
        self.client.force_authenticate(self.owner)
        self.url = reverse("cart-list")
        self.line_url = reverse("cart-item-detail", kwargs={"item_id": self.line.pk})

    def assert_alpine(self, response):
        self.assertEqual(response.status_code, 200, response.data)
        item = next(row for row in response.data["items"] if row["id"] == self.line.pk)
        self.assertEqual(item["product_image"], "/media/products/main.png")
        self.assertEqual(item["product_image_fallbacks"], [
            "/media/products/gallery-first.png", "/media/products/gallery-second.png", "/media/products/later.png",
        ])
        return item

    def test_existing_line_keeps_its_image_after_add_update_and_removal(self):
        self.assert_alpine(self.client.get(self.url))
        item = self.assert_alpine(self.client.post(reverse("cart-add-item"), {"product": self.print.pk, "quantity": 1}))
        self.assertEqual(item["quantity"], 2)
        other = self.product("Valley")
        response = self.client.post(reverse("cart-add-item"), {"product": other.pk, "quantity": 1})
        self.assert_alpine(response)
        unrelated = next(row for row in response.data["items"] if row["product"] == other.pk)
        self.assertIsNone(unrelated["product_image"])
        self.assertEqual(unrelated["product_image_fallbacks"], [])
        response = self.client.patch(self.line_url, {"quantity": 3})
        self.assertEqual(self.assert_alpine(response)["quantity"], 3)
        self.assert_alpine(self.client.delete(reverse("cart-item-detail", kwargs={"item_id": unrelated["id"]})))
        self.assertEqual(CartItem.objects.get(pk=self.line.pk).quantity, 3)

    def test_first_ordered_gallery_image_and_wishlist_when_no_primary(self):
        ProductImage.objects.filter(product=self.print, is_main=True).update(is_main=False)
        item = self.client.get(self.url).data["items"][0]
        self.assertEqual(item["product_image"], "/media/products/gallery-first.png")
        self.assertEqual(item["product_image_fallbacks"][0], "/media/products/gallery-second.png")
        wish = self.client.post(reverse("wishlist-list"), {"product": self.print.pk})
        self.assertIn(wish.status_code, [200, 201], wish.data)
        self.assertEqual(wish.data["product_image"], item["product_image"])

    def test_hidden_product_assets_and_other_shoppers_cannot_leak(self):
        Product.objects.filter(pk=self.print.pk).update(is_active=False)
        self.assertIsNone(self.client.get(self.url).data["items"][0]["product_image"])
        Product.objects.filter(pk=self.print.pk).update(is_active=True, status=Product.StatusChoices.PENDING)
        self.assertEqual(self.client.get(self.url).data["items"][0]["product_image_fallbacks"], [])
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).data["items"], [])
        self.assertEqual(self.client.patch(self.line_url, {"quantity": 2}).status_code, 404)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.url).status_code, 401)

    def test_cart_images_use_constant_queries(self):
        with CaptureQueriesContext(connection) as single:
            self.assert_alpine(self.client.get(self.url))
        for index in range(5):
            product = self.product(str(index))
            self.add_image(product, f"gallery-{index}")
            CartItem.objects.create(cart=self.cart, product=product, quantity=1, unit_price=200)
        with CaptureQueriesContext(connection) as many:
            response = self.client.get(self.url)
        self.assertEqual(len(response.data["items"]), 6)
        self.assertTrue(all(item["product_image"] for item in response.data["items"]))
        self.assertEqual(len(single), len(many))
        self.assertLessEqual(len(many), 4)

    def test_customer_order_detail_uses_same_gallery_and_existing_permissions(self):
        order = Order.objects.create(user=self.owner, subtotal=200, receiver_name="Print customer")
        OrderItem.objects.create(order=order, product=self.print, quantity=1, unit_price=200)
        url = reverse("order-detail", args=[order.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["items"][0]["product_image"], "/media/products/main.png")
        self.assertEqual(response.data["items"][0]["product_image_fallbacks"][0], "/media/products/gallery-first.png")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(url).status_code, 404)
