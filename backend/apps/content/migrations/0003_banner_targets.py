import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("content", "0002_navigationmenu_navigationitem"), ("products", "0011_catalog_integrity")]

    operations = [
        migrations.AddField(
            model_name="banner",
            name="category",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="cms_banners", to="products.category"),
        ),
        migrations.AddField(
            model_name="banner",
            name="product",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name="cms_banners", to="products.product"),
        ),
    ]
