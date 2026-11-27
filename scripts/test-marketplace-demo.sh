#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
project="mb-demo-test-$(cat /proc/sys/kernel/random/uuid)"
compose=(docker compose --env-file /dev/null -p "$project" -f backend/compose.marketplace-demo-test.yaml)
trap '"${compose[@]}" down --volumes' EXIT
"${compose[@]}" run --rm test makemigrations analytics --check --dry-run
"${compose[@]}" run --rm test migrate --noinput
"${compose[@]}" run --rm -e "PGOPTIONS=-c default_transaction_read_only=on" test seed_marketplace_demo --dry-run
suites=(
  apps.analytics.tests.test_marketplace_demo
  apps.analytics.tests.test_seller_analytics_api
  apps.analytics.tests.test_dashboard_analytics_api
  apps.settlements.tests.test_settlement_services
  apps.settlements.tests.test_reconciliation_api
  apps.orders.tests.test_seller_orders
  apps.products.tests.test_catalog_integrity
  apps.shipping.tests.test_multiseller_shipping
  apps.reviews.tests.test_product_reviews_api
)
"${compose[@]}" run --rm test test "${suites[@]}" --noinput
