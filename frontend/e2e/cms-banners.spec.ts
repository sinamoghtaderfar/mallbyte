import { expect, test } from "@playwright/test";
import { adminUser, fulfillJson, installApiMocks } from "./helpers/mock-api";

const image = "https://assets.example.invalid/category.svg";
const banner = (placement: string, title = placement) => ({
  id: ["home_hero", "home_top", "home_middle", "global", "category_top", "checkout"].indexOf(placement) + 1,
  placement, title, subtitle: "Independent print editions", image, mobile_image: image,
  status: "published", is_visible: true, is_clickable: true, link_url: "/products",
  cta_text: "Shop prints", order: 0,
});
const category = { id: 27, name: "Mountain editions", slug: "mountains", parent: null, image, children: [], is_active: true };
const empty = { count: 0, results: [], next: null, previous: null };

test.beforeEach(async ({ page }) => {
  await page.route(image, async route => route.fulfill({
    contentType: "image/svg+xml",
    body: '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="32"><path fill="#a7b38c" d="M0 0h40v32H0z"/></svg>',
  }));
});

test("homepage slots and selected category stay correct on desktop, tablet and mobile", async ({ page }) => {
  test.setTimeout(90_000);
  await installApiMocks(page, {
    "/api/content/homepage/": async route => fulfillJson(route, {
      banners: ["home_hero", "home_top", "home_middle", "global"].map(placement => banner(placement)),
      featured_pages: [], featured_faqs: [], announcements: [],
    }),
    "/api/content/banners/storefront/": async route => {
      const query = new URL(route.request().url()).searchParams;
      const placement = query.get("placement");
      await fulfillJson(route, placement === "global" ? [banner(placement)] : query.get("category") === "27" ? [banner("category_top")] : []);
    },
    "/api/products/categories/": async route => fulfillJson(route, { ...empty, count: 1, results: [category] }),
    "/api/products/categories/27/": async route => fulfillJson(route, category),
    "/api/products/products/": async route => fulfillJson(route, empty),
    "/api/products/brands/": async route => fulfillJson(route, empty),
    "/api/content/navigation/": async route => fulfillJson(route, empty),
  });

  for (const width of [1440, 820, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    for (const placement of ["home_hero", "home_top", "home_middle", "global"]) {
      await expect(page.locator(`[data-placement="${placement}"]`)).toContainText(placement);
    }
    await expect(page.locator('[data-placement="global"]')).toHaveCount(1);
    const card = page.getByRole("link", { name: category.name }).first();
    await expect(card.locator("img")).toHaveAttribute("src", /category\.svg/);
    await card.click();
    await expect(page).toHaveURL(/category=27/);
    await expect(page.locator('[data-placement="category_top"]')).toContainText("category_top");
    await expect(page.getByRole("region", { name: "Selected category" }).locator("img")).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(width);
  }
});

test("existing admin area previews and sends a category upload and explicit removal", async ({ page }) => {
  const writes: string[] = [];
  let row = { ...category };
  await installApiMocks(page, {
    "/api/auth/token/refresh/": async route => fulfillJson(route, { access: "browser-test-session" }),
    "/api/auth/profile/": async route => fulfillJson(route, { user: adminUser }),
    "/api/content/navigation/": async route => fulfillJson(route, empty),
    "/api/products/categories/manage/": async route => fulfillJson(route, { ...empty, count: 1, results: [row] }),
    "/api/products/categories/27/": async route => {
      writes.push(route.request().postDataBuffer()?.toString() ?? "");
      row = { ...row, image: writes.length === 1 ? "/new-campaign.webp" : "" };
      await fulfillJson(route, row);
    },
  });
  await page.goto("/admin/categories");
  await page.getByRole("button", { name: "Edit Mountain editions" }).click();
  await page.getByLabel("Category image").setInputFiles({
    name: "new-campaign.webp", mimeType: "image/webp", buffer: Buffer.from("test upload"),
  });
  // The preview uses the selected browser File. Encoding is validated by Django tests.
  await expect(page.getByRole("img", { name: "Category preview" })).toHaveAttribute("src", /^blob:/);
  await page.getByRole("button", { name: "Save category" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(writes[0]).toContain('filename="new-campaign.webp"');
  await page.getByRole("button", { name: "Edit Mountain editions" }).click();
  await page.getByRole("button", { name: "Remove image" }).click();
  await page.getByRole("button", { name: "Save category" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(writes[1]).toContain('name="image"\r\n\r\n\r\n');
});


test("checkout banner stays separate from placing an order", async ({ page }) => {
  const user = { ...adminUser, is_staff: false, is_superuser: false };
  let orders = 0;
  await installApiMocks(page, {
    "/api/auth/token/refresh/": async route => fulfillJson(route, { access: "browser-test-session" }),
    "/api/auth/profile/": async route => fulfillJson(route, { user }),
    "/api/content/navigation/": async route => fulfillJson(route, empty),
    "/api/content/banners/storefront/": async route => {
      const placement = new URL(route.request().url()).searchParams.get("placement");
      await fulfillJson(route, placement === "checkout" ? [{ ...banner("checkout", "Prints ship flat"), is_clickable: false }] : []);
    },
    "/api/auth/addresses/": async route => fulfillJson(route, { ...empty, count: 1, results: [{
      id: 1, title: "Home", province: "Tehran", city: "Tehran", street: "Print Court",
      building_number: "12", postal_code: "1234567890", receiver_name: "Taylor Print",
      receiver_phone: "09123456789", is_default: true,
    }] }),
    "/api/orders/cart/": async route => fulfillJson(route, {
      id: 1, user: user.id, total_items: 1, subtotal: "940000",
      items: [{ id: 1, product: 10, product_name: "Print", product_sku: "PRINT-01",
        available_stock: 20, quantity: 1, unit_price: "940000", total_price: "940000" }],
    }),
    "/api/orders/orders/checkout/": async route => {
      orders += 1;
      await fulfillJson(route, { id: 55, status: "pending_payment" }, 201);
    },
  });
  await page.goto("/checkout");
  const slot = page.locator('[data-placement="checkout"]');
  await expect(slot).toContainText("Prints ship flat");
  await expect(slot.getByRole("link")).toHaveCount(0);
  await page.getByRole("button", { name: "Place order" }).click();
  await expect(page).toHaveURL(/\/orders\/55$/);
  expect(orders).toBe(1);
});
