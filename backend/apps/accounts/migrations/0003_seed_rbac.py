"""Seed the RBAC permission catalogue and the five system roles (SPEC §6).

Idempotent: `sync_rbac` only adds what is missing, so re-running it (or
`manage.py seed_demo`) never undoes an owner's changes to a role.
"""

from django.db import migrations


def seed(apps, schema_editor):
    from apps.accounts.rbac import sync_rbac

    sync_rbac(apps.get_model("accounts", "Permission"), apps.get_model("accounts", "Role"))


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_rbac_access_devices_mfa"),
    ]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
