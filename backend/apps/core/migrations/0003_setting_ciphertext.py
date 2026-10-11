"""Secret settings are stored encrypted: a ciphertext column, and `value` may be NULL
(ADR-0019)."""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_setting"),
    ]

    operations = [
        migrations.AddField(
            model_name="setting",
            name="ciphertext",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AlterField(
            model_name="setting",
            name="value",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
