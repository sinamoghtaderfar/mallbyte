import random
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.core.management.base import CommandError

DEFAULTS = {
    "sellers": 10,
    "products": 500,
    "categories": 20,
    "customers": 200,
    "orders": 1000,
    "warehouses": 5,
    "reviews": 1500,
    "discounts": 30,
    "tickets": 50,
}


@dataclass(frozen=True)
class Sale:
    number: int
    seller: int
    customer: int
    products: tuple
    placed: datetime
    status: str
    returned: bool
    coupon: int | None


class Plan:
    def __init__(self, sizes, seed, end):
        self.sizes = sizes
        if any(v < 0 for v in sizes.values()) or not all(
            sizes[k] for k in ("sellers", "customers", "warehouses", "categories")
        ):
            raise CommandError(
                "Sellers, customers, warehouses and categories must be positive. Counts cannot be negative."
            )
        if min(sizes["products"], sizes["orders"]) < sizes["sellers"]:
            raise CommandError("Every seller needs at least one product and one order.")
        rng = random.Random(seed)
        self.sales = []
        candidates = {}
        self.sold = Counter()
        self.palettes = [rng.randrange(5) for _ in range(sizes["products"])]
        buyers = list(range(sizes["customers"]))
        rng.shuffle(buyers)
        n = sizes["sellers"]
        for i in range(sizes["orders"]):
            seller, round_no = i % n, i // n
            available = range(seller, sizes["products"], n)
            products = tuple(rng.sample(available, min(len(available), 3)))
            # Stagger first purchases across the year, interleaved with repeat buyers.
            cohort = min(i, i * len(buyers) // sizes["orders"])
            previous = min(i - 1, (i - 1) * len(buyers) // sizes["orders"])
            customer = buyers[cohort if cohort > previous else rng.randint(0, cohort)]
            rounds = len(range(seller, sizes["orders"], n))
            age = 364 - (361 * round_no // max(1, rounds - 1))
            placed = end - timedelta(days=age, minutes=rng.randrange(0, 180))
            status = "delivered"
            if round_no and round_no % 17 == 3:
                status = "cancelled"
            elif rounds > 5 and round_no == rounds - 2:
                status = "shipped"
                placed = end - timedelta(days=2, minutes=seller % 60)
            returned = status == "delivered" and (round_no + seller) % 19 == 14 and round_no > 0
            coupon = i % sizes["discounts"] if sizes["discounts"] and round_no % 4 == 0 else None
            sale = Sale(i + 1, seller, customer, products, placed, status, returned, coupon)
            self.sales.append(sale)
            if status != "cancelled":
                self.sold.update(products)
            if status == "delivered":
                for p in products:
                    if not (returned and p == products[0]):
                        candidates[(customer, p)] = (i + 1, p)
        self.sales.sort(key=lambda sale: sale.placed)
        self.reviews = set(
            rng.sample(list(candidates.values()), min(len(candidates), sizes["reviews"]))
        )
        self.report = dict(
            sizes,
            subcategories=sizes["categories"] * 2,
            stores=n,
            studio_brands=n,
            reviews=len(self.reviews),
            product_images=sizes["products"],
            order_items=sum(len(s.products) for s in self.sales),
            shipments=sum(s.status != "cancelled" for s in self.sales),
            returns=sum(s.returned for s in self.sales),
            payments=len(self.sales),
            payouts=n,
        )
        self.report["refunds"] = self.report["returns"]
        self.report["cancelled_orders"] = sum(s.status == "cancelled" for s in self.sales)
        self.report["completed_orders"] = sum(s.status == "delivered" for s in self.sales)
        self.report["commission_allocations"] = self.report["order_items"]
        self.report["stock_rows"] = sizes["products"]
        self.report["sale_credits"] = sum(self.sold.values())
