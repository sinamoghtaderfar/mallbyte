# Catalog filtering and cart image verification

The screenshots use the same isolated PostgreSQL and media fixture before and after the application change. The marketplace demo seeder created 500 products in 20 parent categories and 40 subcategories. The browser signed in as a test shopper using the isolated mail and PostgreSQL services.

| Scenario | Before | After |
| --- | --- | --- |
| Selecting Celestial Maps from the homepage | [0 results](category-before.jpg) | [25 products from its subcategories](category-after.jpg) |
| Valley Light in Rose · Postcard Set No. 4 | [Letter placeholder](cart-before.jpg) | [Loaded product image on the existing cart line](cart-after.jpg) |

## API behavior

- `/api/products/products/?category=<id>` includes products assigned directly to each selected active category and its active descendants. PostgreSQL resolves the category IDs once before the indexed product filter runs. Repeated `category` parameters keep union semantics. An inactive node stops traversal below it. Invalid or nonexistent IDs return the existing field validation error. The selected ID in the URL still determines the CMS banner.
- Cart reads and item mutations and customer order detail add `product_image` and `product_image_fallbacks` to each item. Eligible media is ordered by primary flag, display order, and then ID. A browser tries the next candidate if an asset fails to load. Cart lines and product variants currently have no variant image relationship. Images on order detail reflect the current public gallery.
- Wishlist and recently viewed use the shared preferred-image selection. Checkout, customer order history, and seller order management have no existing image slot. Their layouts and workflows are unchanged.
- There are no new database migrations or fixture changes. Existing cart rows receive images on their next API request.

## Query measurements

The 500-product demo fixture was expanded to 5,000 products for measurement without generating additional media. Each row uses 31 warmed Django API client requests on the same isolated PostgreSQL 16 database. The query counts include request instrumentation.

| Scenario | Matching rows before → after | SQL statements before → after | Median before → after | p95 before → after |
| --- | ---: | ---: | ---: | ---: |
| Parent category | 0 → 250 | 3 → 6 | 16.74 → 35.58 ms | 25.70 → 51.13 ms |
| One subcategory | 120 → 120 | 5 → 6 | 30.17 → 32.08 ms | 43.65 → 48.94 ms |
| Parent, search, brand, price and sort | 0 → 120 | 4 → 7 | 24.59 → 65.19 ms | 42.78 → 106.32 ms |
| Cart with one item | 1 → 1 | 3 → 4 | 13.27 → 15.06 ms | 20.34 → 28.78 ms |
| Cart with 20 items | 20 → 20 | 3 → 4 | 13.72 → 19.55 ms | 16.55 → 26.23 ms |

The baseline parent requests were empty responses. Cart galleries use one batched query at both cart sizes. The isolated browser verification left all 50 seeded orders and 50 simulated payment records intact.

## Release after approval

In the existing development checkout, after an authorized merge, run the following as its current deployment user. The plan confirms the existing project and persistent volumes.

```bash
git switch main
git pull --ff-only origin main
python3 -B scripts/mallbyte-server.py plan --environment development
python3 -B scripts/mallbyte-server.py deploy --environment development
```

For production, after separate release authorization, run the protected workflow against `main` and complete the existing production environment approval. [Deployment runbook](../deployment.md)

```bash
gh workflow run deploy.yml --repo sinamoghtaderfar/mallbyte --ref main -F authorize_deployment=true
```
