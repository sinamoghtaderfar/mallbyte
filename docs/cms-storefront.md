# Storefront banners and category artwork

Use **Admin → Content & settings → Banners** to upload desktop and optional mobile artwork, select a placement, and save the draft. A publisher can preview it, publish it, or archive it. Published entries display only within their **Visible from** and **Visible until** timestamps. An empty timestamp leaves that end of the window open. Drafts, archives, and entries outside their schedule stay out of the storefront API, including for signed-in publishers. Editing or publishing retains the existing CMS permissions and audit trail.

| Placement | Storefront location | Suggested desktop | Suggested mobile |
| --- | --- | --- | --- |
| `home_hero` | Main homepage feature. The first visible campaign takes the hero slot | 1200 × 1320 | 900 × 960 |
| `home_top` | Directly below the homepage hero | 1280 × 560 | 800 × 640 |
| `home_middle` | Between featured products and new arrivals | 1280 × 560 | 800 × 640 |
| `category_top` | Above catalog results when a category filter is active | 1280 × 560 | 800 × 640 |
| `product_detail` | Below primary product information and before reviews | 1280 × 560 | 800 × 640 |
| `checkout` | Compact strip above shipping and order controls | 960 × 480 | 640 × 320 |
| `global` | Top of home, catalog, product, cart, checkout, FAQ, and public CMS pages | 960 × 480 | 640 × 320 |

Smaller display-order numbers appear first. When orders tie, newer entries come first, followed by the internal record ID for a stable order. The homepage payload caps each placement independently. Images switch to the mobile upload below 640 CSS pixels when available. Banner artwork fits inside the panel so the entire composition stays visible. Include essential promotional text in the title or subtitle so screen readers and customers can read it without relying on text inside the image. JPEG, PNG, and WebP uploads accept up to 8 MB.

Select a category for **Category Top** to show the banner only on the corresponding filter. A general entry appears when the chosen active category has no currently visible match. For **Product Detail**, choose a product, choose its category, or leave both blank. The storefront uses the first tier with visible matches in the order **product → category → general**. Search target selectors by catalog name. Clear a banner’s target before deleting the referenced item.

A banner with **Clickable** enabled displays its button when it has a valid site-relative or HTTPS URL. Checkout banners are best used for shipping or packaging information. The banner only follows a link when the customer activates it. Admin, seller, warehouse, finance and sign-in workspaces do not load storefront promotions.

The storefront uses the existing homepage payload and a public read action on the existing banner resource. The action sends `Cache-Control: private, no-store`. Page entry or a category/product change fetches current data, and simultaneous identical requests share one response. Reload an already-open page to see a subsequent publication or schedule change. For a missing banner, check placement, targeting, order, publication status, and the time window. Browse to the same page as a shopper. An API failure leaves the affected slot empty and the surrounding workflow usable.

## Category images

Open **Admin → Product categories** at `/admin/categories`. Product administrators and superusers can add or edit categories. Choose a JPEG, PNG, or WebP with a matching file extension. The editor shows the selected file before saving. Use **Remove image** and save to clear the image, or leave the field alone to preserve it. Replacement files receive fresh public media URLs, so a reload retrieves changed artwork. Clearing or replacing a reference retains the old uploaded file in storage.

Use a 1200 × 960 image with the focal subject near the center. The homepage category grid uses responsive cards and crops artwork to fit. Catalog browsing shows the selected category and links to its immediate subcategories. Both views show an illustrated fallback for missing or unavailable media. Next.js uses native images and picture sources for these slots. When uploading succeeds but the image fails to load, check the reported media URL and your gateway’s public media route.

## Backfill missing demo artwork

Fresh demo seeding generates deterministic artwork for every category. Existing demo installations can preview or apply the same artwork through the recorded `MarketplaceDemoRun` provenance. Backfill preserves every nonempty image reference and only updates image fields on manifest-owned categories. It does not seed any other dataset. Cleanup removes generated demo files it owns and retains manually uploaded files.

From the intended development checkout, use the actual development database name. This example matches the root development Compose database.

```bash
docker compose exec -T \
  -e MARKETPLACE_DEMO_ENV=development -e MARKETPLACE_DEMO_DATABASE=mallbyte \
  -e 'PGOPTIONS=-c default_transaction_read_only=on' \
  web python manage.py backfill_marketplace_demo_category_images --dry-run
```

After approving the reported changes, apply and then confirm a replay creates zero images.

```bash
docker compose exec -T \
  -e MARKETPLACE_DEMO_ENV=development -e MARKETPLACE_DEMO_DATABASE=mallbyte \
  web python manage.py backfill_marketplace_demo_category_images

docker compose exec -T \
  -e MARKETPLACE_DEMO_ENV=development -e MARKETPLACE_DEMO_DATABASE=mallbyte \
  -e 'PGOPTIONS=-c default_transaction_read_only=on' \
  web python manage.py backfill_marketplace_demo_category_images --dry-run
```

The JSON report shows `created`, `would_create`, `skipped`, `failed`, and any failed category IDs. A nonempty but broken image reference is skipped and can be repaired manually. The schema migration `content.0003_banner_targets` adds nullable banner targets and leaves existing banners and uploads in place.

[Chromium screenshots for all seven placements and the category grid](cms-placement-verification/README.md)
