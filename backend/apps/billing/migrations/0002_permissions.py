"""Billing and notification permissions (ADR-0012): `billing.view`, `billing.manage`,
`notifications.view` and `notifications.manage` join the catalogue.

Seed roles that already exist get the new grants their defaults now include
(`sync_rbac` only grants defaults to roles it creates): admin all four, support and
viewer the two `.view` codes.
"""

from django.db import migrations

NEW_GRANTS = {
    "admin": ("billing.view", "billing.manage", "notifications.view", "notifications.manage"),
    "support": ("billing.view", "notifications.view"),
    "viewer": ("billing.view", "notifications.view"),
}


def grant(apps, schema_editor):
    from apps.accounts.rbac import sync_rbac

    Permission = apps.get_model("accounts", "Permission")
    Role = apps.get_model("accounts", "Role")
    sync_rbac(Permission, Role)
    for role in Role.objects.filter(name__in=NEW_GRANTS):
        role.permissions.add(*Permission.objects.filter(code__in=NEW_GRANTS[role.name]))


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0004_library_permissions"),
        ("billing", "0001_initial"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
