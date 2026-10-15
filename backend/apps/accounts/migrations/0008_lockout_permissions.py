"""The Lockouts page's permissions (Q1, ADR-0023): `lockouts.view` and `lockouts.manage`.

Seed roles that already exist get the grants their defaults now include (`sync_rbac`
only grants defaults to roles it creates): admin and support both, viewer the view.
Owners hold every permission anyway.
"""

from django.db import migrations

NEW_GRANTS = {
    "admin": ("lockouts.view", "lockouts.manage"),
    "support": ("lockouts.view", "lockouts.manage"),
    "viewer": ("lockouts.view",),
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
        ("accounts", "0007_webauthn_and_auth_epoch"),
    ]

    operations = [migrations.RunPython(grant, migrations.RunPython.noop)]
