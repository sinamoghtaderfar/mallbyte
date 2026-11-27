from collections import Counter, defaultdict
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import qrcode
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import CommandError
from django.db.models import F
from django.utils.text import slugify

from apps.accounts.models import Seller, User
from apps.content.models import Announcement, Banner, ContentPage, FAQCategory, FAQItem
from apps.discounts.models import Discount, DiscountUsage
from apps.inventory.models import Stock, StockMovement, Warehouse
from apps.observability.models import RequestLog, SystemHealthSnapshot
from apps.observability.workflow_models import Incident, IncidentEvent
from apps.orders.models import (
    Order,
    OrderItem,
    OrderStatusHistory,
    SellerOrderFulfillment,
    SellerOrderFulfillmentHistory,
)
from apps.payments.models import Payment
from apps.products.models import Brand, Category, Product, ProductImage
from apps.rbac.models import Role, UserRole
from apps.rbac.utils import has_permission
from apps.returns.models import ReturnItem, ReturnRequest, ReturnStatusHistory
from apps.reviews.models import ProductReview
from apps.reviews.services import update_product_review_stats
from apps.settlements.models import ConfirmedPayout, FinancialAuditRun, SellerLedgerEntry
from apps.settlements.reconciliation import reconciliation_report
from apps.settlements.services import (
    build_order_allocations,
    post_captured_payment,
    post_confirmed_payout,
    post_verified_refund,
)
from apps.shipping.models import Shipment, ShipmentEvent
from apps.support.models import SupportTicket, TicketMessage

from .catalog import COLLECTIONS, FORMATS, PALETTES, STUDIOS, artwork, category_artwork, category_image_name, identity, studio_name

CITIES = ("Tehran", "Tabriz", "Shiraz", "Isfahan", "Rasht")
FIRST = "Mina Arman Leila Kian Sara Navid Nika Ramin Tara Darya Kaveh Nima Roya Azin Homa".split()
LAST = "Bahari Mehran Javid Shayan Nikfar Rad Azimi Nouri Rahimi Rasti Salimi Zandi Parsa Saba Darzi".split()


def customer_email(i):
    return f"print-buyer-{i + 1}@marketplace-demo.invalid"


def preflight(emails, protected, sizes, *, dry_run):
    customers = {customer_email(i) for i in range(sizes["customers"])}
    if protected in customers or protected in emails or customers.intersection(emails):
        raise CommandError("Demo identities overlap a protected or reserved account.")
    if User._base_manager.filter(email__in=customers).exists():
        raise CommandError("A customer identity already exists outside the demo manifest.")
    existing = {}
    users = User._base_manager.all()
    profiles = Seller.objects.all()
    if not dry_run:
        users = users.select_for_update()
        profiles = profiles.select_for_update()
    for i, email in enumerate(emails):
        user = users.filter(email__iexact=email).first()
        if user is None:
            continue
        profile = profiles.filter(user=user).first()
        if (
            not user.is_active
            or user.is_deleted
            or user.is_staff
            or user.is_superuser
            or not user.is_seller
            or not has_permission(user, "view_seller_analytics")
            or has_permission(user, "view_platform_analytics")
            or profile is None
            or profile.status != "approved"
            or not 0 < profile.commission_rate < 100
        ):
            raise CommandError(
                f"Seller slot {i + 1} is occupied by an incompatible account. Its identity and grants are protected."
            )
        existing[i] = profile
    # Natural-key collisions must never adopt another operator's fixture.
    planned_keys = (
        (Product, "sku", "MBDEMO-P{}", "products"),
        (Order, "order_number", "MBDEMO-O{}", "orders"),
        (Payment, "payment_number", "MBDEMO-PAY{}", "orders"),
        (Discount, "code", "MBDEMO-PRINT{:02}", "discounts"),
        (Warehouse, "code", "MBDEMO-W{}", "warehouses"),
        (SupportTicket, "ticket_number", "MBDEMO-T{}", "tickets"),
        (ConfirmedPayout, "external_reference", "MBDEMO-{}-payout", "sellers"),
    )
    for model, key, pattern, name in planned_keys:
        keys = [pattern.format(i + 1) for i in range(sizes[name])]
        if model.objects.filter(**{key + "__in": keys}).exists():
            raise CommandError(
                f"A planned key already exists in {model._meta.label}. No records adopted."
            )
    for i in set(range(len(emails))) - existing.keys():
        if Seller.objects.filter(store_name=studio_name(i)).exists():
            raise CommandError(f"Store name for seller slot {i + 1} already exists.")
    return existing


class Fixture:
    def __init__(self, inventory, plan, seed, end):
        self.inv = inventory
        self.create = inventory.create
        self.plan = plan
        self.n = plan.sizes
        self.seed = seed
        self.end = end
        self.start = end - timedelta(days=365)
        self.stores = []
        self.brands = []
        self.buyers = []
        self.warehouses = []
        self.products = []
        self.codes = []
        self.reused = {}
        self.remaining = {}
        self.movements = []
        self.totals = defaultdict(Counter)
        self.reviewed = set()

    def stores_and_buyers(self, emails, existing):
        role = Role.objects.get(name="vendor")
        for i, email in enumerate(emails):
            name = studio_name(i)
            profile = existing.get(i)
            description = (
                STUDIOS[i % len(STUDIOS)][1] + ". Small-batch archival prints and paper goods."
            )
            filled = {}
            for field in ("logo", "banner"):
                if not profile or not getattr(profile, field):
                    filled[field] = self.inv.image(f"{field}-{i}", identity(i, field == "banner"))
            if not profile or not profile.description:
                filled["description"] = description
            if profile:
                Seller.objects.filter(pk=profile.pk).update(**filled)
                self.reused[i] = {"pk": profile.pk, "user": profile.user_id, "filled": filled}
            else:
                user = self.create(
                    User,
                    email=email,
                    password=make_password(None),
                    full_name=name,
                    is_seller=True,
                    email_verified=True,
                    date_joined=self.start,
                )
                self.create(UserRole, user=user, role=role)
                profile = self.create(
                    Seller,
                    self.start,
                    user=user,
                    store_name=name,
                    store_slug=f"mbdemo-{slugify(name)}",
                    status="approved",
                    business_email=email,
                    business_phone=f"000000{i + 1:05}",
                    verified_at=self.start,
                    commission_rate=8 + i % 7,
                    **filled,
                )
            self.stores.append(profile)
            # The storefront product card displays brand_name as the maker.
            self.brands.append(
                self.create(
                    Brand,
                    self.start,
                    name=f"{profile.store_name[:75]} Studio Editions",
                    slug=f"mbdemo-studio-{i + 1}",
                    description=profile.description or description,
                    logo=filled.get("logo", profile.logo.name),
                )
            )
        first_purchase = {s.customer: s.placed for s in reversed(self.plan.sales)}
        for i in range(self.n["customers"]):
            name = f"{FIRST[i % len(FIRST)]} {LAST[i // len(FIRST) % len(LAST)]}"
            self.buyers.append(
                self.create(
                    User,
                    email=customer_email(i),
                    full_name=name,
                    password=make_password(None),
                    email_verified=True,
                    date_joined=first_purchase.get(i, self.end) - timedelta(hours=12),
                )
            )

    def catalog(self):
        categories = []
        for i in range(self.n["categories"]):
            name, _ = COLLECTIONS[i % len(COLLECTIONS)]
            suffix = "" if i < len(COLLECTIONS) else f" Series {1 + i // len(COLLECTIONS)}"
            parent = self.create(
                Category,
                self.start,
                name=name + suffix,
                slug=f"mbdemo-{i}-{slugify(name)}",
                order=i,
            )
            categories.append(
                [
                    self.create(
                        Category,
                        self.start,
                        name=fmt,
                        parent=parent,
                        slug=f"{parent.slug}-{slugify(fmt)}",
                    )
                    for fmt in FORMATS
                ]
            )
            for category in (parent, *categories[-1]):
                file = self.inv.image(category_image_name(category), category_artwork(category))
                Category.objects.filter(pk=category.pk).update(image=file)
        for i in range(self.n["warehouses"]):
            city = CITIES[i % len(CITIES)]
            self.warehouses.append(
                self.create(
                    Warehouse,
                    self.start,
                    name=f"{city} Print Depot {i + 1}",
                    code=f"MBDEMO-W{i + 1}",
                    city=city,
                    province=city,
                    address=f"Unit {i + 2}, Printmakers Court",
                    type="main" if i == 0 else "branch",
                )
            )
        first_order = {p: sale.placed for sale in reversed(self.plan.sales) for p in sale.products}
        for i in range(self.n["products"]):
            # Distinct publication times keep the storefront's newest-first pagination stable.
            released = first_order.get(i, self.end) - timedelta(hours=4, microseconds=i + 1)
            c = i % len(categories)
            edition = i // len(categories) + 1
            fmt = (edition - 1) % 2
            palette = PALETTES[self.plan.palettes[i]]
            subject = COLLECTIONS[c % len(COLLECTIONS)][1]
            name = f"{subject} in {palette[0]} · {FORMATS[fmt]} No. {edition}"
            description = (
                f"{self.stores[i % len(self.stores)].store_name}'s {subject.lower()} study in a "
                f"{palette[0].lower()} palette. "
                + (
                    "An unframed 30 × 40 cm print on 250 gsm matte paper."
                    if not fmt
                    else "A boxed set of six A6 postcards with uncoated writing backs."
                )
            )
            slug = f"mbdemo-{i + 1}-{slugify(subject)}"

            image = self.inv.image(f"edition-{i}", artwork(c, palette, f"{self.seed}:{i}"))
            price = (260000 if fmt else 940000) + (i * 7 % 17) * 10000
            product = self.create(
                Product,
                released,
                seller=self.stores[i % len(self.stores)].user,
                category=categories[c][fmt],
                brand=self.brands[i % len(self.brands)],
                name=name,
                slug=slug,
                sku=f"MBDEMO-P{i + 1}",
                qr_code=f"{self.inv.folder}/qr-{i}.png",
                status="approved",
                is_active=True,
                is_featured=i % 9 == 0,
                description=description,
                short_description=description,
                price=price,
                approved_at=released,
                views_count=20 + i * 17 % 470,
            )
            qr = qrcode.make(f"{settings.FRONTEND_URL}/products/{product.pk}")
            Product.objects.filter(pk=product.pk).update(qr_code=self.inv.image(f"qr-{i}", qr))
            self.create(
                ProductImage, released, product=product, image=image, is_main=True, alt_text=name
            )
            self.products.append(product)
            initial = self.plan.sold[i] + (2 if i % 21 == 0 else 12 + i % 27)
            self.remaining[i] = 0
            self.movement(i, initial, "purchase", "Opening print run", released)
        for i in range(self.n["discounts"]):
            self.codes.append(
                self.create(
                    Discount,
                    self.start,
                    code=f"MBDEMO-PRINT{i + 1:02}",
                    title=f"Studio print offer {i + 1}",
                    value=5 + i % 11,
                    start_at=self.start,
                    end_at=self.end + timedelta(days=30),
                )
            )

    def movement(self, i, quantity, kind, reference, at):
        before = self.remaining[i]
        self.remaining[i] += quantity
        obj = StockMovement(
            product=self.products[i],
            warehouse=self.warehouses[i % len(self.warehouses)],
            movement_type=kind,
            quantity=quantity,
            reference_id=reference,
            before_quantity=before,
            after_quantity=self.remaining[i],
        )
        self.movements.append((obj, at))

    def sale(self, s):
        buyer, seller = self.buyers[s.customer], self.stores[s.seller].user
        city = CITIES[s.customer % len(CITIES)]
        address = {
            "receiver_name": buyer.full_name,
            "receiver_phone": f"000000{s.customer:05}",
            "city": city,
            "province": city,
            "address": f"{12 + s.customer}, Willow Lane",
            "postal_code": f"{s.customer:010}",
        }
        cancelled = s.status == "cancelled"
        paid = None if cancelled else s.placed + timedelta(minutes=4)
        shipped = s.placed + timedelta(hours=15)
        delivered = s.placed + timedelta(days=2) if s.status == "delivered" else None
        subtotal = sum(self.products[p].price for p in s.products)
        code = self.codes[s.coupon] if s.coupon is not None and not cancelled else None
        discount = subtotal * code.value // 100 if code else 0
        order = self.create(
            Order,
            s.placed,
            order_number=f"MBDEMO-O{s.number}",
            user=buyer,
            status=s.status,
            payment_status="failed" if cancelled else "paid",
            subtotal=subtotal,
            discount_amount=discount,
            shipping_cost=35000,
            paid_at=paid,
            delivered_at=delivered,
            cancelled_at=s.placed + timedelta(minutes=10) if cancelled else None,
            **address,
        )
        items = []
        for p in s.products:
            product = self.products[p]
            items.append(
                self.create(
                    OrderItem,
                    s.placed,
                    order=order,
                    product=product,
                    warehouse=self.warehouses[p % len(self.warehouses)],
                    quantity=1,
                    unit_price=product.price,
                )
            )
        if code:
            self.create(
                DiscountUsage,
                s.placed,
                discount=code,
                order=order,
                user=buyer,
                code_snapshot=code.code,
                discount_amount=discount,
            )
            code.used_count += 1
        allocations = build_order_allocations(order)
        for row in allocations:
            self.inv.own(row)
        pay = self.create(
            Payment,
            s.placed,
            payment_number=f"MBDEMO-PAY{s.number}",
            order=order,
            user=buyer,
            provider="mock",
            amount=order.total_amount,
            currency="IRR",
            status="failed" if cancelled else "success",
            paid_at=paid,
            gateway_reference="" if cancelled else f"mbdemo-capture-{s.number}",
            failed_at=s.placed + timedelta(minutes=3) if cancelled else None,
            failure_reason="Simulated declined payment" if cancelled else "",
        )
        self.create(OrderStatusHistory, s.placed, order=order, new_status="pending_payment")
        self.totals[s.seller]["total_orders"] += 1
        if cancelled:
            self.create(
                OrderStatusHistory,
                order.cancelled_at,
                order=order,
                old_status="pending_payment",
                new_status="cancelled",
            )
        else:
            for row in post_captured_payment(pay):
                self.inv.own(row)
            for p in s.products:
                self.movement(p, -1, "sale", f"payment:{pay.pk}", paid)
            self.fulfill(order, seller, paid, shipped, delivered, address)
            total = self.totals[s.seller]
            total["total_sales"] += subtotal - discount
            total["balance"] += sum(a.seller_payable for a in allocations)
            if s.returned:
                reversal = self.refund(order, pay, items[0], allocations[0], delivered)
                total["balance"] -= reversal
                total["total_sales"] -= allocations[0].merchandise_net
            for p, item in zip(s.products, items, strict=True):
                if (s.number, p) in self.plan.reviews:
                    self.review(s, p, item, delivered)
        return order

    def fulfill(self, order, seller, paid, shipped, delivered, address):
        fulfillment = self.create(
            SellerOrderFulfillment, paid, order=order, seller=seller, status=order.status
        )
        steps = [
            ("pending_payment", "paid", paid),
            ("paid", "processing", paid + timedelta(hours=1)),
            ("processing", "shipped", shipped),
        ]
        if delivered:
            steps.append(("shipped", "delivered", delivered))
        for old, new, at in steps:
            self.create(OrderStatusHistory, at, order=order, old_status=old, new_status=new)
            self.create(
                SellerOrderFulfillmentHistory,
                at,
                fulfillment=fulfillment,
                old_status=old,
                new_status=new,
            )
        shipment = self.create(
            Shipment,
            shipped,
            order=order,
            seller_fulfillment=fulfillment,
            user=order.user,
            shipment_number=f"{order.order_number}-S",
            carrier="post",
            status=order.status,
            tracking_number=f"{order.order_number}-TRACK",
            shipping_cost=order.shipping_cost,
            shipped_at=shipped,
            delivered_at=delivered,
            **address,
        )
        self.create(
            ShipmentEvent,
            shipped,
            shipment=shipment,
            old_status="ready_to_ship",
            new_status="shipped",
        )
        if delivered:
            self.create(
                ShipmentEvent,
                delivered,
                shipment=shipment,
                old_status="shipped",
                new_status="delivered",
            )

    def refund(self, order, payment, item, allocation, delivered):
        at = delivered + timedelta(hours=4)
        amount = allocation.merchandise_net
        ret = self.create(
            ReturnRequest,
            at,
            request_number=f"{order.order_number}-R",
            customer=order.user,
            order=order,
            status="refund_pending",
            reason="damaged",
            requested_resolution="refund",
            refund_method="original_payment",
            total_requested_amount=amount,
            total_approved_amount=amount,
            reviewed_at=at,
            customer_note="The print arrived with a creased corner.",
        )
        self.create(
            ReturnItem,
            at,
            return_request=ret,
            order_item=item,
            quantity=1,
            reason="damaged",
            condition="damaged",
            status="refunded",
            requested_refund_amount=amount,
            approved_refund_amount=amount,
        )
        key = f"{order.order_number}-refund"
        confirmed = at + timedelta(hours=1)
        refund = self.inv.own(
            post_verified_refund(
                payment_id=payment.pk,
                return_request_id=ret.pk,
                confirmed_amount=amount,
                currency="IRR",
                gateway_reference=key,
                idempotency_key=key,
                confirmed_at=confirmed,
                lines=[{"order_item_id": item.pk, "merchandise": amount}],
            )
        )
        line = self.inv.own(refund.items.get())
        self.inv.own(SellerLedgerEntry.objects.get(refund=refund))
        ReturnRequest.objects.filter(pk=ret.pk).update(
            status="refunded", closed_at=confirmed, updated_at=confirmed
        )
        self.create(
            ReturnStatusHistory,
            confirmed,
            return_request=ret,
            old_status="refund_pending",
            new_status="refunded",
        )
        return line.seller_reversal

    def review(self, sale, product_index, item, delivered):
        at = delivered + timedelta(hours=3)
        value = (sale.number + product_index) % 7
        comments = (
            "Arrived flat and the ink coverage is even.",
            "The colors look lovely in daylight.",
            "Crisp details, although I would prefer a heavier stock.",
            "The packaging protected every corner.",
        )
        self.create(
            ProductReview,
            at,
            customer=self.buyers[sale.customer],
            product=item.product,
            order_item=item,
            rating=(3, 4, 5, 4, 5, 5, 5)[value],
            title=item.product.name.split(" · ")[0],
            comment=comments[value % 4],
            status="pending" if value == 0 else "approved",
            is_verified_purchase=True,
            approved_at=None if value == 0 else at,
        )
        self.reviewed.add(item.product)

    def finish(self, orders):
        # Historical stock movements are replayed without calling the live low-stock notification hook.
        rows = StockMovement.objects.bulk_create([m for m, _ in self.movements], batch_size=500)
        for movement, (_, at) in zip(rows, self.movements, strict=True):
            self.inv.own(movement)
            movement.created_at = at
        StockMovement.objects.bulk_update(rows, ["created_at"], batch_size=300)
        for i, quantity in self.remaining.items():
            self.create(
                Stock,
                product=self.products[i],
                warehouse=self.warehouses[i % len(self.warehouses)],
                quantity=quantity,
                reserved_quantity=0,
                low_stock_threshold=5,
            )
        for product in self.reviewed:
            update_product_review_stats(product)
        Discount.objects.bulk_update(self.codes, ["used_count"])
        for i, store in enumerate(self.stores):
            total = self.totals[i]
            # Only disburse a share of this fixture's first mature sale. Never spend a reused seller's prior balance.
            payout_amount = min(total["balance"] // 4, self.first_credits[i] // 4)
            key = f"MBDEMO-{i + 1}-payout"
            payout = self.inv.own(
                post_confirmed_payout(
                    seller_id=store.user_id,
                    amount=payout_amount,
                    currency="IRR",
                    external_reference=key,
                    idempotency_key=key,
                    confirmed_at=self.end,
                )
            )
            self.inv.own(SellerLedgerEntry.objects.get(source_key=f"payout:{payout.pk}"))
            total["balance"] -= payout.amount
            deltas = {
                k: int(Decimal(v).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
                for k, v in total.items()
            }
            Seller.objects.filter(pk=store.pk).update(**{k: F(k) + v for k, v in deltas.items()})
            if i in self.reused:
                self.reused[i]["totals"] = deltas
        for i in range(self.n["tickets"]):
            order = orders[(i * len(orders) // max(1, self.n["tickets"])) % len(orders)]
            at = order.paid_at or order.cancelled_at
            subjects = (
                "Can I get a copy of my receipt?",
                "Which frame fits this print?",
                "Where can I track the parcel?",
            )
            status = ("open", "pending", "resolved")[i % 3]
            ticket = self.create(
                SupportTicket,
                at,
                ticket_number=f"MBDEMO-T{i + 1}",
                order=order,
                customer=order.user,
                subject=subjects[i % 3],
                category=("payment", "product", "shipping")[i % 3],
                status=status,
                priority=("normal", "low", "high")[i % 3],
                last_message_at=at,
                resolved_at=at + timedelta(hours=1) if status == "resolved" else None,
            )
            self.create(
                TicketMessage, at, ticket=ticket, sender=order.user, message=subjects[i % 3]
            )
        self.content()
        self.monitoring()

    def monitoring(self):
        # The financial monitoring view consumes a persisted reconciliation result.
        # Keep existing global alert state intact.
        audit = reconciliation_report()
        count = audit["total_findings"]
        self.create(
            FinancialAuditRun,
            run_key="mbdemo-print-market-audit",
            source="manual",
            status="warnings" if count else "clean",
            summary=audit,
            findings_count=count,
            attempts=1,
            started_at=self.end,
            finished_at=self.end,
        )
        for i in range(24):
            self.create(
                RequestLog,
                self.end - timedelta(minutes=i * 15),
                method="GET",
                path="/api/products/products/",
                request_id=f"mbdemo-catalog-{i}",
                status_code=200,
                duration_ms=20 + i * 7 % 48,
                metadata={"simulation": "marketplace-demo"},
            )
        for i in range(6):
            self.create(
                SystemHealthSnapshot,
                self.end - timedelta(hours=i),
                hostname="simulated-print-market",
                status="healthy",
                database_status="healthy",
                redis_status="healthy",
                celery_status="healthy",
                celery_beat_status="healthy",
                cpu_percent=11 + i * 3,
                memory_percent=33 + i,
                disk_percent=21,
                database_latency_ms=2 + i % 3,
                metadata={"simulation": "marketplace-demo"},
            )
        incident = self.create(
            Incident,
            self.end - timedelta(days=1),
            title="Demo exercise: delayed print catalog refresh",
            description="Simulated fulfillment exercise. The refreshed catalog was verified.",
            severity="low",
            status="resolved",
            resolved_at=self.end,
        )
        self.create(
            IncidentEvent,
            self.end,
            incident=incident,
            event_type="status",
            from_value="investigating",
            to_value="resolved",
            note="Demo stock and catalog totals reconciled.",
        )

    def content(self):
        published = {"status": "published", "published_at": self.end - timedelta(days=6)}
        self.create(
            ContentPage,
            title="Caring for your print",
            slug="mbdemo-print-care",
            page_type="custom",
            content="Handle the edges with dry hands. Frame behind glass and hang away from direct sun.",
            **published,
        )
        self.create(
            Banner,
            title="Independent print studios",
            placement="home_hero",
            image=self.inv.image("market-banner", identity(0, True)),
            **published,
        )
        self.create(
            Announcement,
            title="Print market is open",
            message="Explore the new independent studio collections.",
            **published,
        )
        category = self.create(FAQCategory, name="Print market help", slug="mbdemo-print-help")
        self.create(
            FAQItem,
            category=category,
            question="Are frames included?",
            answer="Art prints ship unframed.",
        )

    def populate(self, emails, existing):
        self.stores_and_buyers(emails, existing)
        self.catalog()
        self.first_credits = {}
        orders = []
        for sale in self.plan.sales:
            orders.append(self.sale(sale))
            if sale.seller not in self.first_credits:
                self.first_credits[sale.seller] = self.totals[sale.seller]["balance"]
        self.finish(orders)
