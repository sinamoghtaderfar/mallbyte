from django.db.models import F, IntegerField, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce

from apps.products.models import ProductImage


def ordered_product_images(queryset):
    return queryset.exclude(image="").order_by("-is_main", "order", "pk")


def product_images_prefetch(prefix="", *, include_fallbacks=False):
    images = ordered_product_images(ProductImage.objects.all())
    if not include_fallbacks:
        images = images[:1]
    return Prefetch(prefix + "images", queryset=images, to_attr="_display_images")


def product_image_url(product):
    if hasattr(product, "_display_images"):
        image = next(iter(product._display_images), None)
    else:
        image = ordered_product_images(product.images.all()).first()
    return image.image.url if image else None


def product_image_urls(product):
    images = getattr(product, "_display_images", None)
    if images is None:
        images = ordered_product_images(product.images.all())
    return list(dict.fromkeys(image.image.url for image in images))


def catalog_queryset(queryset):
    return queryset.select_related("brand", "category").prefetch_related(product_images_prefetch()).annotate(
        _catalog_stock=Coalesce(Sum("stock_items__quantity"), Value(0), output_field=IntegerField()),
        _catalog_reserved=Coalesce(Sum("stock_items__reserved_quantity"), Value(0), output_field=IntegerField()),
        _catalog_available=Coalesce(
            Sum(
                F("stock_items__quantity") - F("stock_items__reserved_quantity"),
                filter=Q(stock_items__warehouse__is_active=True),
            ),
            Value(0), output_field=IntegerField(),
        ),
    ).order_by("-created_at", "-pk")
