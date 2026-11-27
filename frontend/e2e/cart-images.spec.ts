import { expect, test } from "@playwright/test";
import { adminUser, fulfillJson, installApiMocks } from "./helpers/mock-api";

const api = "http://localhost:8000";
const primary = "/media/products/unavailable.png";
const gallery = "/media/products/print.png";
const productName = "Valley postcard";

test("cart loads gallery fallback after refresh and quantity changes", async ({ page }) => {
  const owner = { ...adminUser, is_staff: false, is_superuser: false };
  let quantity = 1;
  function cart() {
    return {
      id: 10, user: owner.id, total_items: quantity, subtotal: String(100 * quantity),
      items: [{ id: 11, product: 12, product_name: productName, product_sku: "PRINT-1", product_price: "100",
        product_image: primary, product_image_fallbacks: [gallery], available_stock: 5,
        quantity, unit_price: "100", total_price: String(100 * quantity) }],
    };
  }
  await installApiMocks(page, {
    "/api/auth/token/refresh/": async (route) => fulfillJson(route, { access: "browser-fixture" }),
    "/api/auth/profile/": async (route) => fulfillJson(route, { user: owner }),
    "/api/orders/cart/": async (route) => fulfillJson(route, cart()),
    "/api/orders/cart/items/11/": async (route) => {
      expect(route.request().method()).toBe("PATCH");
      quantity = route.request().postDataJSON().quantity;
      await fulfillJson(route, cart());
    },
  });
  await page.route(api + primary, (route) => route.fulfill({ status: 404 }));
  await page.route(api + gallery, (route) => route.fulfill({
    contentType: "image/png",
    body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/iZk9HQAAAABJRU5ErkJggg==", "base64"),
  }));
  await page.setViewportSize({ width: 390, height: 840 });
  await page.goto("/cart");
  const image = page.getByAltText(productName, { exact: true });
  await expect(image).toHaveAttribute("src", api + gallery);
  await expect.poll(() => image.evaluate((element: HTMLImageElement) => element.naturalWidth)).toBe(1);
  await page.reload();
  await expect(image).toHaveAttribute("src", api + gallery);
  await page.getByRole("button", { name: "+", exact: true }).click();
  await expect(page.getByLabel("Quantity 2")).toBeVisible();
  await expect(image).toHaveAttribute("src", api + gallery);
  await expect(page.getByRole("link", { name: "Continue to checkout" })).toHaveAttribute("href", "/checkout");
});
