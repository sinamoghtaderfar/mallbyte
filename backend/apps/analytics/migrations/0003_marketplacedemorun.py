from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("analytics", "0002_private_report_storage")]
    operations = [
        migrations.CreateModel(
            name="MarketplaceDemoRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("fingerprint", models.CharField(max_length=64)),
                ("as_of", models.DateField()),
                ("rows", models.JSONField(default=dict)),
                ("files", models.JSONField(default=list)),
                ("reused_stores", models.JSONField(default=list)),
                ("report", models.JSONField(default=dict)),
            ],
        ),
    ]
