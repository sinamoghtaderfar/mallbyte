# Seed the development marketplace

`seed_marketplace_demo` installs a fictional independent print market in an explicitly selected development PostgreSQL database. It is a manual Django management command. The migration adds one provenance table. Deployment, startup, migrations, and Celery do not run the command.

## Verify in disposable Docker

Run the Docker verification script from the repository root.

```bash
./scripts/test-marketplace-demo.sh
```

The script uses a unique Compose project, an internal network, PostgreSQL 16 on tmpfs, temporary media, and read-only application source mounts. It checks the migration, migrates the disposable database, prints a JSON preview, then runs the seeder and regression tests in Django's separate test database. It removes its own project on exit. It publishes no ports and does not load the root Compose override or root environment file.

Docker Compose can build the backend from `backend/Dockerfile` when the development image is unavailable. A fresh checkout can also use a dedicated test image.

```bash
export MARKETPLACE_TEST_IMAGE=mallbyte-marketplace-tests:local
docker build -t "$MARKETPLACE_TEST_IMAGE" backend
./scripts/test-marketplace-demo.sh
```

## Preview on the intended development database

The following commands use the monorepo's running development service named `web`. Run them only when you intend to access that environment. The preview works before the new provenance migration if the existing marketplace schema is current.

Set the database name independently to the intended target. Set the **full seller prefix**, including the plus tag and the word `seller`, without an index or `@`. For example, the fictional prefix `studio+seller` and domain `example.invalid` produce `studio+seller1@example.invalid` through `studio+seller10@example.invalid`. Use your local prefix, domain, and existing superuser address when previewing your own fixture. The prompts keep these values out of shell history.

```bash
cd /home/moghtader/projects/mallbyte
export MARKETPLACE_DEMO_ENV=development
read -rp 'Exact development database name: ' MARKETPLACE_DEMO_DATABASE
read -rp 'Seller email prefix (including +seller): ' MARKETPLACE_DEMO_SELLER_PREFIX
read -rp 'Seller email domain: ' MARKETPLACE_DEMO_SELLER_DOMAIN
read -rsp 'Existing protected superuser email: ' MARKETPLACE_DEMO_SUPERUSER_EMAIL
echo
export MARKETPLACE_DEMO_DATABASE MARKETPLACE_DEMO_SELLER_PREFIX
export MARKETPLACE_DEMO_SELLER_DOMAIN MARKETPLACE_DEMO_SUPERUSER_EMAIL

demo_seed() {
  docker compose exec -T \
    -e MARKETPLACE_DEMO_ENV -e MARKETPLACE_DEMO_DATABASE \
    -e MARKETPLACE_DEMO_SELLER_PREFIX -e MARKETPLACE_DEMO_SELLER_DOMAIN \
    -e MARKETPLACE_DEMO_SUPERUSER_EMAIL \
    web python manage.py seed_marketplace_demo "$@"
}

demo_seed --dry-run
```

The report identifies how many seller accounts and profiles would be created or reused. Counts describe the fixture, independent of any unrelated data already in the database. The preview generates no rows, images, credentials, or external requests. Database uniqueness and referential constraints are also checked during the actual write.

## Install after approval

After the operator approves the target and preview, apply the provenance migration and install the same plan. These commands use the shell function and variables above. For a preview that must remain identical across midnight, pass the same explicit `--as-of YYYY-MM-DD` to both preview and install.

```bash
docker compose exec -T web python manage.py migrate analytics
demo_seed
demo_seed --dry-run
```

The last command returns `"action": "unchanged"` and `"creates": 0`. The command checks its recorded row keys and media paths on replay. A different seed, date, quantity, or identity configuration requires a fresh database or explicit demo cleanup. After installation, omitting `--as-of` reuses the recorded date.

All primary sizes are configurable. This example previews two studios with 24 products and 60 orders.

```bash
demo_seed --dry-run --seed 19 --as-of 2026-11-26 \
  --sellers 2 --products 24 --categories 4 --customers 16 \
  --orders 60 --warehouses 2 --reviews 50 --discounts 4 --tickets 8
```

## Default dry-run and accounting

| Data | Default fixture |
| --- | ---: |
| Approved sellers / stores | 10 / 10 |
| Top-level categories / subcategories | 20 / 40 |
| Products / main product images | 500 / 500 |
| Fictional customers | 200 |
| Orders / order lines | 1,000 / 3,000 |
| Delivered / shipped / cancelled orders | 930 / 10 / 60 |
| Mock payments / successful captures | 1,000 / 940 |
| Shipments | 940 |
| Warehouses / stock rows | 5 / 500 |
| Verified-purchase reviews | 1,500 |
| Discounts / support tickets | 30 / 50 |
| Damaged-item returns / confirmed partial refunds | 48 / 48 |
| Checkout allocations / seller sale credits | 3,000 / 2,820 |
| Confirmed seller payouts | 10 |

Each default store has its own studio brand, 50 products and 100 single-seller orders spread across the preceding year. Checkout snapshots apply the store's commission rate and apportion discounts and shipping. Only successful simulated captures credit the ledger. Returns reverse the affected merchandise and commission. A damaged print stays out of sellable inventory. Each seller receives a simulated payout from its own demo earnings. Amounts are in IRR. Profile totals count all fixture orders and report net merchandise sales and whole-rial balances. The ledger retains fractional precision.

The review target is capped by distinct eligible customer/product purchases on delivered orders. Smaller configurations may produce fewer reviews. Order histories, ticket messages, low-stock items, published store content, a saved reconciliation result, and explicitly simulated observability data supply the relevant dashboards. The reconciliation result covers the connected database and can include pre-existing findings.

Product artwork, logos, and banners are original procedural PNG illustrations rendered locally with Pillow. The merchandise images depict the editions being sold. These graphics may be reused under the [backend MIT license](../backend/LICENSE). The generation needs no image host, API key, remote license lookup, or media archive. Fixed inputs recreate business keys, purchase history, ratings and artwork. Database IDs, per-install media directories and operational timestamps are allocated at installation.

## Isolated verification example

With seed `314159` and history ending `2026-11-26`, a PostgreSQL read-only preview on an empty database reported the default counts above and zero reused accounts. Reuse counts depend on the selected database. The ledger reconciled to a remaining seller payable of 1,678,281,687 IRR.

| Amount | IRR |
| --- | ---: |
| Successful captures | 1,944,830,400 |
| Platform shipping | 32,900,000 |
| Refunded merchandise | 34,734,500 |
| Commission on captured sales | 198,510,750 |
| Commission reversed on refunds | 3,594,906 |
| Confirmed payouts | 3,998,369 |
| Remaining payable across all sellers | 1,678,281,687 |

Remaining payable equals captures less shipping, merchandise refunds, net commission, and payouts. The platform reconciliation service reported zero findings. A repeat installation created zero rows. Two simultaneous installs against a fresh disposable target also produced one creation and one unchanged result with the settlement policy enabled. Chromium loaded rendered product imagery, seller dashboard/analytics/finance, and the administrative control center, analytics, finance, audit monitoring, super-admin overview, observability, and incident workflow screens against the disposable fixture.

## Identity, storage, and cleanup

The command requires the development base settings with DEBUG enabled, an explicit `development` or `demo` environment, and a PostgreSQL database name matching both configuration and the live connection. Existing active, approved sellers with seller analytics access can be reused. The command rejects staff, superusers, suspended or deleted accounts, incompatible roles, and conflicting customer identities. It preserves existing credentials, MFA enrollment and RBAC assignments. A new seller gets the migrated vendor role. New accounts have unusable passwords and follow the application's existing sign-in flow. Seeding never requests OTP delivery, calls a gateway or queues a transfer.

A transaction-scoped PostgreSQL advisory lock serializes install and cleanup. The database manifest owns specific primary keys and the generated files under `MEDIA_ROOT/marketplace-demo/`. Reused sellers keep their identity and existing artwork. The fixture can fill empty branding fields and add its own financial totals. Cleanup subtracts those deltas and clears only branding values still matching the supplied demo values.

```bash
demo_seed --cleanup --dry-run
# Run the next command after approving cleanup on this target.
demo_seed --cleanup
```

Cleanup refuses to delete claimed or privileged accounts and rolls back if unrelated records depend on demo rows. This can include a wishlist, an authentication profile, or request history created after a user signs in. Preserve those dependencies or use a disposable database for sessions that need unrestricted resets. The command retains files shared by surviving records and removes unshared demo media after database commit. Retry cleanup if file removal is interrupted. A hard process kill during initial media generation can leave an unreferenced run directory because filesystem writes are outside the database transaction.

The source, migration, renderer, tests, and runbook belong in Git. Runtime media, local configuration, generated credentials, and database dumps belong in ignored local paths such as `.local/` or `backups/`. Existing frontend/backend ports, SMTP settings, Compose overrides and development volumes need no changes.
